"""Ce que Jervis a le droit de faire seul, ce qu'il doit te demander, et ce qui est interdit.

Claude Code appelle le `Gardien` avant chaque outil qui n'est pas en lecture seule :
une commande shell, un outil Gmail ou Agenda, une page web, un clic dans une app…
"""

import asyncio
import os
import re
import shlex
import subprocess
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from . import appels, comptes, config


class Decision(Enum):
    OUI = "oui"            # il le fait sans demander
    DEMANDER = "demander"  # il te pose la question à voix haute et attend « oui »
    NON = "non"            # interdit, il te dira qu'il ne peut pas


def politique(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = (),
              mode: str | None = None) -> Decision:
    """Décide pour un appel d'outil, selon config.CONFIRMATIONS (« autonome » par défaut, puis « confiance »,
    « essentielles » et « toutes », du moins au plus prudent)."""
    mode = mode or config.CONFIRMATIONS
    if mode == "autonome":
        return politique_autonome(outil, entree, apps_vues)
    if mode == "toutes":
        return politique_prudente(outil, entree, apps_vues)
    if mode == "essentielles":
        return politique_essentielle(outil, entree, apps_vues)
    return politique_confiance(outil, entree, apps_vues)


# Un nom de commande Bash compte s'il est en position de commande (début, après ; && | $( ` ou xargs, exec…).
_COMMANDE = r"(?:^|[;&|(`]|\b(?:xargs|exec|nohup|then|do|time|env|sudo)\s)\s*(?:\S*/)?"


# --- Mode « confiance » (par défaut) : s'il te l'a demandé, c'est oui -----------------------------
# Envoyer le message, le mail, l'invitation qu'il t'a demandés : sans question, il en prend la responsabilité.
# Ne restent confirmés que l'argent, ce qui détruit pour de bon, les secrets, le système (terminal, lui-même),
# et le code lancé dans ton Chrome connecté. (Si l'idée vient d'une page ou d'un mail et pas de toi, le prompt lui
# dit d'appeler demander_accord : c'est la protection contre les pages piégées.)

# Le nom d'une action qui paie, achète, détruit ou publie à tout le monde. Un mot entier du nom
# (delete_event, createOrder, buy_domain), pour que « notebook » ou « reorder » ne demandent rien.
ACTION_LOURDE = re.compile(
    r"(?:(?<![a-zA-Z])|(?<=[a-z])(?=[A-Z]))(?i:delete|remove|destroy|erase|purge|pay|payment|purchase|checkout"
    r"|buy|transfer|refund|charge|order|book|booking|subscribe|publish|tweet)s?(?![a-z])")
# Bash : comme BASH_A_CONFIRMER, mais envoyer un mail (mail, sendmail, osascript … send) ne demande plus rien.
BASH_LOURD = re.compile(
    _COMMANDE + r"(rm|rmdir|unlink|shred|srm|dd|kill|killall|pkill|launchctl|crontab|security|tccutil|csrutil"
    r"|spctl|chown|passwd|dscl|systemsetup|networksetup|ssh|scp|sftp)\b"
    r"|\s-delete\b|\bdefaults\s+delete|\bchmod\s+-R|\bdiskutil\s+(?!list|info)|\bpmset\s+(?!-g)"
    r"|\bgit\s+(push|reset\s+--hard|clean|branch\s+-D|checkout\s+--|restore|stash\s+drop)"
    r"|\b(npm|yarn|pnpm|cargo|gem|twine)\s+publish|\b(brew|pip3?|npm|uv)\s+(uninstall|remove|rm)\b"
    r"|\|\s*(sudo\s+)?(ba|z|fi)?sh\b|\|\s*(python3?|perl|ruby|node)\b(?!\s+-[mc]\b)|\beval\b"
    r"|\bcurl\b.*(\s-d\s|--data|\s-F\s|--form|\s-T\s|--upload|-X\s*(POST|PUT|DELETE|PATCH))"
    r"|\bosascript\b.*\b(delete|empty)\b"
    r"|\b(ba|z|da|fi|k|tc|c)?sh\b[^|;&]*\s-[a-zA-Z]*c|\bdo\s+shell\s+script\b"
    r"|\bchmod\b[^|;&]*(\s[0-7]*[1357][0-7]*\b|[+=][rwXst]*x)",
    re.IGNORECASE | re.DOTALL,
)
# Ce qui se relance tout seul (démarrage, shell, tâches planifiées) : y écrire, c'est s'installer pour de bon.
PERSISTANCE = re.compile(
    r"(LaunchAgents|LaunchDaemons|StartupItems|login ?items?|/etc/|\.(zshrc|zshenv|zprofile|zlogin|zlogout|bashrc"
    r"|bash_profile|bash_login|profile|gitconfig)\b|\.config/(fish|git)\b|\.git/(hooks|config)\b|\.pth\b|sitecustomize|usercustomize"
    r"|crontab|periodic/"
    # écrire (>, tee, cp, mv, ln) dans un fichier caché du dossier perso ou dans ~/Library
    r"|(>|\b(tee|cp|mv|ln|install|rsync)\b)[^|;&]*(~|\$HOME|\$\{HOME\}|/Users/[^/\s]+)/(\.|Library/))",
    re.IGNORECASE,
)
# Apps où un geste peut lancer une commande, toucher aux mots de passe ou à lui-même (les messageries n'y sont plus).
APPS_SYSTEME = re.compile(
    r"\b(terminal|iterm2?|warp|ghostty|alacritty|kitty|wezterm|hyper"
    r"|trousseau|keychain|mots de passe|passwords|1password|bitwarden|jervis|python[\d.]*)\b",
    re.IGNORECASE,
)
# Menus et boutons qui détruisent, paient ou touchent au système (« Envoyer » n'y est plus).
GESTE_LOURD = re.compile(
    r"supprimer|delete|effacer|erase|vider|empty|r[ée]initialiser|reset|payer|pay|acheter|buy|commander"
    r"|d[ée]connex|log ?out|red[ée]marrer|restart|[ée]teindre|shut ?down|autoriser|allow",
    re.IGNORECASE,
)


def nom_action(outil: str) -> str:
    """Le nom de l'action, à la façon de Gmail ou du Drive (« search_threads »). Notion écrit les siens avec des
    tirets et son nom devant (« notion-search », « notion-create-pages ») : ils deviennent « search », « create_pages »."""
    action = outil.split("__")[-1]
    if outil.startswith("mcp__claude_ai_") and "-" in action:
        action = re.sub(r"^[a-z]+-", "", action).replace("-", "_")
    return action


def politique_confiance(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = ()) -> Decision:
    action = nom_action(outil)
    if outil.startswith("mcp__jervis__"):  # iMessage : envoyé direct ; demander_accord : c'est lui qui veut demander
        return _maison(action, entree, {"demander_accord", *COMPTES_A_CONFIRMER})
    if outil.startswith(CHROME_PERSO):  # du code dans ton Chrome connecté (banque, mails…) : toujours demandé
        return _chrome_perso(action, entree)
    if outil.startswith("mcp__navigateur__"):
        return Decision.DEMANDER if action == "browser_file_upload" else Decision.OUI
    if outil.startswith("mcp__mac__"):
        return _mac_confiance(action, entree, apps_vues)
    if outil.startswith("mcp__ecran__"):
        return Decision.OUI
    if outil.startswith("mcp__"):
        if action == "share_file":  # à une personne : oui ; à tout le monde qui a le lien, ou au domaine : demandé
            return Decision.DEMANDER if re.search(r"anyone|domain", str(entree), re.IGNORECASE) else Decision.OUI
        if action == "execute_zapier_write_action":  # Slack, Notion… : oui, sauf une action qui paie ou détruit
            return Decision.DEMANDER if ACTION_LOURDE.search(str(entree.get("action", ""))) else Decision.OUI
        if action.startswith(LECTURE) or action in ACTIONS and action != "delete_event":
            return Decision.OUI
        return Decision.DEMANDER if ACTION_LOURDE.search(action) else Decision.OUI
    if outil in LECTURE_FICHIERS | ECRITURE_FICHIERS:
        chemin = chemin_fichier(entree)
        if FICHIERS_SECRETS.search(chemin) or recherche_trop_large(outil, entree):
            return Decision.DEMANDER
        if outil in ECRITURE_FICHIERS and (PERSISTANCE.search(chemin) or dossier_sensible(chemin)):
            return Decision.DEMANDER  # ~/Library, ~/.config… : là où se cachent les démarrages automatiques
        return Decision.OUI
    if outil == "Bash":
        commande = entree.get("command", "").strip()
        if INTERDIT.search(commande) or SUPPRESSION_DIRECTE.search(commande):
            return Decision.NON
        if (BASH_LOURD.search(commande) or TELECHARGE_ET_LANCE.search(commande)
                or FICHIERS_SECRETS.search(commande) or PERSISTANCE.search(commande)):
            return Decision.DEMANDER
        return Decision.OUI
    return Decision.OUI


def politique_autonome(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = ()) -> Decision:
    """Par défaut : il fait tout seul, et il en prend la responsabilité (mails, messages, comptes, publications,
    mots de passe, fichiers, corbeille, Chrome). Il ne te demande que pour l'argent : demander_accord, et un outil
    qui paie (quand JERVIS_PAIEMENTS=oui : sinon motif_paiement les refuse avant d'arriver ici). Ce qui est interdit
    d'office (sudo, rm, kill…) reste refusé : il passe par la corbeille. Pour le voir te demander davantage :
    JERVIS_CONFIRMATIONS=confiance (ou essentielles, toutes)."""
    action = nom_action(outil)
    decision = politique_confiance(outil, entree, apps_vues)
    if decision is not Decision.DEMANDER:
        return decision
    if action == "demander_accord" or (config.PAIEMENTS and (ACTION_QUI_PAIE.search(action)
                                                             or ACTION_QUI_PAIE.search(str(entree.get("action", ""))))):
        return Decision.DEMANDER
    return Decision.OUI


def recherche_trop_large(outil: str, entree: dict[str, Any]) -> bool:
    """Grep ou Glob sur tout le dossier perso (ou plus haut) : ils passeraient par les dossiers à secrets."""
    if outil not in {"Grep", "Glob"}:
        return False
    base = str(entree.get("path") or "")
    motifs = [str(entree.get("glob") or "")] + ([str(entree.get("pattern") or "")] if outil == "Glob" else [])
    racines = [base] if base else []
    for motif in filter(None, motifs):
        if ".." in motif or re.search(r"\{[^}]*(?:[/~]|[{,]\.)", motif):
            return True  # « ../.ssh/* », « {../.ssh,x} », « {/Users/…,x} » : on ne sait pas où il irait
        fixe = re.split(r"[*?\[{]", motif)[0]
        fixe = fixe if fixe.endswith("/") or fixe == motif else fixe.rsplit("/", 1)[0] if "/" in fixe else ""
        if fixe.startswith(("/", "~")):
            racines.append(fixe or "/")
        elif fixe:
            racines.append(str(Path(base or config.ESPACE) / fixe))
    return any(dossier_sensible(r) for r in racines)  # sans chemin : son dossier de travail, sans secrets


def dossier_sensible(chemin: str) -> bool:
    """Le dossier perso entier (ou plus haut), ~/Library ou un dossier caché du dossier perso (~/.ssh…)."""
    try:
        dossier = Path(chemin).expanduser()
        dossier = (dossier if dossier.is_absolute() else config.ESPACE / dossier).resolve()  # son dossier de travail
    except (OSError, RuntimeError):
        return True
    maison = Path.home().resolve()
    if dossier == maison or dossier in maison.parents:
        return True
    if not dossier.is_relative_to(maison):
        return False
    premier = dossier.relative_to(maison).parts[0].casefold()  # le disque du Mac ignore la casse : ~/LIBRARY
    return premier == "library" or premier.startswith(".")


def _mac_confiance(action: str, entree: dict[str, Any], apps_vues: Iterable[str]) -> Decision:
    if action == "see":
        return Decision.DEMANDER if APPS_SECRETES.search(nom_app(entree.get("app_target"))) else Decision.OUI
    if action == "dialog":
        return Decision.DEMANDER if GESTE_LOURD.search(str(entree.get("button") or "")) else Decision.OUI
    if action not in GESTES_MAC:
        return Decision.OUI
    apps = {(_app_devant() or INCONNUE) if a == INCONNUE else a for a in cibles_mac(action, entree, apps_vues)}
    if any(a != INCONNUE and APPS_SYSTEME.search(a) for a in apps):
        return Decision.DEMANDER
    if action == "menu" and GESTE_LOURD.search(f"{entree.get('item', '')} {entree.get('path', '')}"):
        return Decision.DEMANDER
    return Decision.OUI


# --- Mode « essentielles » : il fait tout seul, sauf ce qui ne se rattrape pas ----------------------
# Ce qui reste confirmé : ce qui part chez quelqu'un d'autre, ce qui détruit pour de bon, les secrets,
# l'argent. Tout le reste (ouvrir, créer, modifier, ranger, cliquer, coder) se fait sans question.

# Connecteurs : ce qui envoie, partage ou supprime chez les autres.
CONNECTEUR_A_CONFIRMER = {"send_message", "reply", "forward", "share_file", "execute_zapier_write_action",
                          "request_access", "respond_to_event", "delete_event"}
# Un outil inconnu (nouveau connecteur, nouvelle version) : on demande si son nom parle d'envoyer, payer…
ACTION_ENGAGEANTE = re.compile(
    r"send|share|publish|post|delete|remove|pay|purchase|checkout|transfer|invite|tweet|order|book", re.IGNORECASE)
# Bash : ce qui efface, tue un programme, touche au système, pousse du code, envoie des données.
# Un nom de commande compte s'il est en position de commande (début, après ; && | $( ` ou xargs, exec…),
# pas au milieu d'un texte (open -a "Mail" ne demande rien, mail -s … oui).
BASH_A_CONFIRMER = re.compile(
    _COMMANDE + r"(rm|rmdir|unlink|shred|srm|dd|kill|killall|pkill|launchctl|crontab|security|tccutil|csrutil"
    r"|spctl|chown|passwd|dscl|systemsetup|networksetup|ssh|scp|sftp|mail|sendmail)\b"
    r"|\s-delete\b|\bdefaults\s+delete|\bchmod\s+-R|\bdiskutil\s+(?!list|info)|\bpmset\s+(?!-g)"
    r"|\bgit\s+(push|reset\s+--hard|clean|branch\s+-D|checkout\s+--|restore|stash\s+drop)"
    r"|\b(npm|yarn|pnpm|cargo|gem|twine)\s+publish|\b(brew|pip3?|npm|uv)\s+(uninstall|remove|rm)\b"
    r"|\|\s*(sudo\s+)?(ba|z|fi)?sh\b|\|\s*(python3?|perl|ruby|node)\b(?!\s+-[mc]\b)|\beval\b"  # télécharger puis exécuter
    r"|\bcurl\b.*(\s-d\s|--data|\s-F\s|--form|\s-T\s|--upload|-X\s*(POST|PUT|DELETE|PATCH))"  # envoyer des données
    r"|\bosascript\b.*\b(send|delete|empty)\b",                                   # envoyer, supprimer par AppleScript
    re.IGNORECASE | re.DOTALL,
)
# Apps du Mac où un geste peut parler à quelqu'un, lancer une commande, toucher aux mots de passe ou à lui-même.
APPS_ESSENTIELLES = re.compile(
    r"\b(terminal|iterm2?|warp|ghostty|alacritty|kitty|wezterm|hyper"
    r"|trousseau|keychain|mots de passe|passwords|1password|bitwarden|jervis|python[\d.]*"
    r"|messages|whatsapp|mail|slack|discord|telegram|signal|teams|outlook|facetime|messenger|instagram)\b",
    re.IGNORECASE,
)
# Menus et boutons de dialogue qui envoient ou détruisent pour de bon (« Mettre à la corbeille » se rattrape).
GESTE_DEFINITIF = re.compile(
    r"envoyer|send|supprimer|delete|effacer|erase|vider|empty|r[ée]initialiser|reset|payer|pay|acheter|buy"
    r"|d[ée]connex|log ?out|red[ée]marrer|restart|[ée]teindre|shut ?down|autoriser|allow",
    re.IGNORECASE,
)


# Ton Chrome à toi, piloté par l'extension Claude in Chrome. Naviguer, lire, cliquer, taper : seul (les étapes
# qui engagent passent par demander_accord, comme ailleurs). Lancer du code dans la page ou envoyer un fichier
# du Mac : il demande, car ça peut toucher tes sessions ouvertes (banque, mails…).
CHROME_PERSO = "mcp__claude-in-chrome__"
CHROME_A_CONFIRMER = {"javascript_tool", "file_upload", "upload_image"}


def _chrome_perso(action: str, entree: dict[str, Any]) -> Decision:
    if action in CHROME_A_CONFIRMER:
        return Decision.DEMANDER
    if action == "browser_batch" and any(m in str(entree) for m in CHROME_A_CONFIRMER):
        return Decision.DEMANDER  # un lot qui cache du code ou un envoi de fichier
    return Decision.OUI


def politique_essentielle(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = ()) -> Decision:
    action = nom_action(outil)
    if outil.startswith("mcp__jervis__"):
        return _maison(action, entree, JERVIS_A_CONFIRMER)
    if outil.startswith(CHROME_PERSO):
        return _chrome_perso(action, entree)
    if outil.startswith("mcp__navigateur__"):  # son Chrome à lui : tout, sauf envoyer un fichier du Mac
        return Decision.DEMANDER if action == "browser_file_upload" else Decision.OUI
    if outil.startswith("mcp__mac__"):
        return _mac_essentiel(action, entree, apps_vues)
    if outil.startswith("mcp__ecran__"):  # screenpipe : il ne fait que relire ce qui est passé à l'écran
        return Decision.OUI
    if outil.startswith("mcp__"):
        if action in CONNECTEUR_A_CONFIRMER:
            return Decision.DEMANDER
        if action in {"create_event", "update_event"} and entree.get("attendees"):  # l'invitation part aux invités
            return Decision.DEMANDER
        if action.startswith(LECTURE) or action in ACTIONS:  # lire, ou une action connue qui reste chez lui
            return Decision.OUI
        return Decision.DEMANDER if ACTION_ENGAGEANTE.search(action) else Decision.OUI
    if outil in LECTURE_FICHIERS | ECRITURE_FICHIERS:
        return Decision.DEMANDER if FICHIERS_SECRETS.search(chemin_fichier(entree)) else Decision.OUI
    if outil == "Bash":
        commande = entree.get("command", "").strip()
        if INTERDIT.search(commande) or SUPPRESSION_DIRECTE.search(commande):
            return Decision.NON
        if BASH_A_CONFIRMER.search(commande) or FICHIERS_SECRETS.search(commande):
            return Decision.DEMANDER
        return Decision.OUI
    return Decision.OUI  # WebSearch, WebFetch, Task…


def _mac_essentiel(action: str, entree: dict[str, Any], apps_vues: Iterable[str]) -> Decision:
    if action == "see":
        return Decision.DEMANDER if APPS_SECRETES.search(nom_app(entree.get("app_target"))) else Decision.OUI
    if action == "dialog":
        return Decision.DEMANDER if GESTE_DEFINITIF.search(str(entree.get("button") or "")) else Decision.OUI
    if action not in GESTES_MAC:  # regarder, ouvrir, fermer, quitter, presse-papiers…
        return Decision.OUI
    apps = cibles_mac(action, entree, apps_vues)
    if any(a != INCONNUE and APPS_ESSENTIELLES.search(a) for a in apps):
        return Decision.DEMANDER
    if action == "menu" and GESTE_DEFINITIF.search(f"{entree.get('item', '')} {entree.get('path', '')}"):
        return Decision.DEMANDER
    return Decision.OUI


# --- Mode « toutes » : l'ancienne politique, prudente --------------------------------------------

def politique_prudente(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = ()) -> Decision:
    """Décide pour un appel d'outil.

    `outil` vaut par exemple :
      - "Bash"  → la commande est dans entree["command"], ex. 'open -a "Cursor"'
      - "WebSearch" / "WebFetch"
      - "mcp__claude_ai_Google_Calendar__list_events"  (lire ton agenda)
      - "mcp__claude_ai_Google_Calendar__create_event" (créer un rendez-vous)
      - "mcp__claude_ai_Gmail__search_threads" / "...__send_message"
      - "mcp__mac__click" (cliquer dans une app du Mac ; `apps_vues` = les apps qu'il vient de regarder)

    Règle par défaut : tout ce qui lit ou ouvre se fait seul, tout ce qui envoie, crée,
    modifie ou supprime te demande d'abord. Change-la comme tu veux.
    """
    if outil in {"WebSearch", "WebFetch"}:
        return Decision.OUI
    if outil.startswith("mcp__jervis__"):  # ses outils maison (outils.py)
        return _maison(outil.split("__")[-1], entree, JERVIS_A_CONFIRMER)
    if outil.startswith(CHROME_PERSO):  # ton Chrome à toi, via l'extension Claude
        return _chrome_perso(outil.split("__")[-1], entree)
    if outil.startswith("mcp__navigateur__"):  # son Chrome : naviguer, lire, cliquer, taper → seul
        action = outil.split("__")[-1]
        return Decision.DEMANDER if action.startswith(NAVIGATEUR_A_CONFIRMER) else Decision.OUI
    if outil.startswith("mcp__mac__"):  # les apps du Mac (Peekaboo)
        return _politique_mac(outil.split("__")[-1], entree, apps_vues)
    if outil.startswith("mcp__ecran__"):  # screenpipe : lecture seule
        return Decision.OUI
    if outil.startswith("mcp__"):
        action = nom_action(outil)
        return Decision.OUI if action.startswith(LECTURE) else Decision.DEMANDER
    if outil in {"Task", "Agent"}:  # il confie une tâche à son expert : chaque outil de l'expert repasse par ici
        return Decision.OUI
    if outil in LECTURE_FICHIERS:  # lire et chercher dans tes fichiers : seul, sauf les secrets
        return Decision.DEMANDER if FICHIERS_SECRETS.search(chemin_fichier(entree)) else Decision.OUI
    if outil == "Bash":
        commande = entree.get("command", "").strip()
        if INTERDIT.search(commande) or SUPPRESSION_DIRECTE.search(commande):
            return Decision.NON
        if SANS_RISQUE.match(commande) and not re.search(r"[;&|`$<>]", commande):
            return Decision.OUI
    return Decision.DEMANDER


# Outils maison confirmés hors du mode « confiance » : ceux qui parlent à quelqu'un d'autre que toi, et ceux
# qui effacent (la corbeille se rattrape, donc en mode « confiance » ils passent sans question).
# Créer un compte, sortir un mot de passe du Trousseau, lire un code reçu par SMS : demandé dans tous les modes. Tu
# entends le site exact (« netflix.com », pas « netflix-connexion.xyz »), et un code ne part pas sur la demande d'une
# page piégée qui essaierait de te voler un compte en le réinitialisant.
COMPTES_A_CONFIRMER = {"creer_mot_de_passe", "copier_mot_de_passe", "chercher_code_recu", "ranger_cle_api"}
JERVIS_A_CONFIRMER = {"envoyer_imessage", "appeler", "demander_accord", "mettre_a_la_corbeille", "effacer_memoire",
                      *COMPTES_A_CONFIRMER}


def chemins_a_jeter(entree: dict[str, Any]) -> list[str]:
    """Ce que mettre_a_la_corbeille va jeter (il accepte une liste ou un seul chemin)."""
    chemins = entree.get("chemins") or entree.get("chemin") or []
    return [str(c) for c in ([chemins] if isinstance(chemins, str) else chemins)]


def _maison(action: str, entree: dict[str, Any], a_confirmer: set[str]) -> Decision:
    """Ses outils maison (outils.py). Jeter une clé, un mot de passe, un réglage de Claude : demandé dans tous
    les modes, comme pour les lire."""
    if action == "mettre_a_la_corbeille" and any(FICHIERS_SECRETS.search(c) for c in chemins_a_jeter(entree)):
        return Decision.DEMANDER
    return Decision.DEMANDER if action in a_confirmer else Decision.OUI
# Dans le navigateur : exécuter du code, envoyer un fichier, toucher aux cookies et sessions → il demande.
# (Avant de payer, commander, envoyer… il doit aussi appeler demander_accord : voir brain.py.)
NAVIGATEUR_A_CONFIRMER = ("browser_run_code", "browser_evaluate", "browser_file_upload", "browser_cookie",
                          "browser_localstorage", "browser_sessionstorage", "browser_set_storage", "browser_route")
# Actions des connecteurs qui ne font que lire (Gmail, Agenda, Drive, Notion, et le catalogue de Zapier).
LECTURE = ("list", "get", "search", "suggest", "read", "download", "fetch",
           "inspect_zapier", "discover_zapier", "execute_zapier_read")
# Commandes Bash que Jervis lance sans demander (une seule commande, sans enchaînement).
SANS_RISQUE = re.compile(
    r"""^(open\s+(-a\s+|"https?://|'https?://|https?://)"""
    r"""|osascript\s+-e\s+['"](set\s+volume|output\s+volume|tell\s+application\s+"(Spotify|Music)"\s+to\s+(play|pause|playpause|next\s+track|previous\s+track))"""
    r"""|date\b|pmset\s+-g|ls\b|cal\b)""",
    re.IGNORECASE,
)
INTERDIT = re.compile(r"\b(sudo|rm\s+-rf|mkfs|diskutil\s+erase|shutdown|reboot)\b")
# Supprimer en direct : jamais. Tout ce qui s'efface passe par la corbeille (mettre_a_la_corbeille), qui se rattrape.
SUPPRESSION_DIRECTE = re.compile(_COMMANDE + r"(rm|rmdir|unlink|shred|srm)\b|\bfind\b[^|;&]*\s-delete\b", re.IGNORECASE)
# Une commande qui télécharge puis lance d'un coup : « bash <(curl …) » (« curl … | sh » est déjà dans BASH_LOURD).
TELECHARGE_ET_LANCE = re.compile(r"<\(\s*(?:curl|wget)\b", re.IGNORECASE)
# Fichiers : lire et chercher se fait seul ; écrire (Write, Edit) demande, une fois par dossier et par demande.
LECTURE_FICHIERS = {"Read", "Glob", "Grep"}
ECRITURE_FICHIERS = {"Write", "Edit"}
FICHIERS_SECRETS = re.compile(
    r"(\.ssh|\.gnupg|\.aws|\.config/gh|Keychains|\.netrc|\.npmrc|\.pypirc|\.env\b|credentials|secret"
    r"|id_rsa|id_ed25519|\.pem$|\.key$|Cookies|Login Data|\.claude/)",
    re.IGNORECASE,
)


def chemin_fichier(entree: dict[str, Any]) -> str:
    """Le fichier ou dossier que visent Read, Glob, Grep, Write et Edit (avec le motif, pour Glob et Grep)."""
    return " ".join(str(entree.get(k) or "") for k in ("file_path", "path", "pattern", "glob")).strip()


# --- Les apps du Mac (Peekaboo) -------------------------------------------------------------------
# Apps où un clic ou une frappe peut lancer une commande, envoyer un message à quelqu'un, toucher à la
# sécurité du Mac ou à lui-même : là, chaque geste est confirmé. Partout ailleurs, il agit seul.
APPS_SENSIBLES = re.compile(
    r"\b(terminal|iterm2?|warp|ghostty|alacritty|kitty|wezterm|hyper"           # terminaux
    r"|[ée]diteur de script|script editor|scripteditor\d*|automator|raccourcis|shortcuts"
    r"|trousseau|keychain|r[ée]glages|settings|pr[ée]f[ée]rences|preferences"   # sécurité du Mac
    r"|mots de passe|passwords|1password|bitwarden"
    r"|cursor|code|vscode|xcode|zed|claude|jervis|python[\d.]*"                   # code, et lui-même
    r"|messages|whatsapp|mail|slack|discord|telegram|signal|teams|outlook|facetime)\b",  # messageries
    re.IGNORECASE,
)
# Parmi elles, les ateliers de code et d'IA : il y travaille souvent en plusieurs gestes (ouvrir le chat, coller
# un prompt, valider). Il te demande une seule fois par demande et par app, au lieu d'avant chaque geste.
# (Les terminaux, les messageries et lui-même restent confirmés geste par geste.)
APPS_ATELIER = re.compile(r"\b(cursor|code|vscode|visual studio code|xcode|zed|windsurf|claude)\b", re.IGNORECASE)
# Apps à secrets : même les regarder demande ton accord (ce qu'il voit part chez Claude).
APPS_SECRETES = re.compile(r"\b(trousseau|keychain|mots de passe|passwords|1password|bitwarden)\b", re.IGNORECASE)
# Menus qui détruisent, ferment ou envoient.
MENU_RISQUE = re.compile(
    r"quitter|quit|supprimer|delete|effacer|erase|vider|empty|corbeille|trash|fermer|close|envoyer|send"
    r"|d[ée]conne|log ?out|red[ée]marrer|restart|[ée]teindre|shut ?down|r[ée]initialiser|reset",
    re.IGNORECASE,
)
# Raccourcis ⌘ qui quittent, ferment ou suppriment (⌘Q, ⌘W, ⌘⌫…).
TOUCHES_RISQUE = {"q", "w", "delete", "backspace", "del", "forwarddelete", "escape", "esc"}
INCONNUE = "?"  # tout l'écran, une fenêtre par numéro… : on ne sait pas quelle app reçoit le geste
# Gestes qui agissent dans l'app visée (via le « snapshot » de see, ou une app nommée).
GESTES_MAC = {"click", "type", "press", "set_value", "action", "paste", "menu"}


def _lancer(*commande: str) -> str:
    try:
        return subprocess.run(commande, capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _app_devant() -> str:
    """Le nom de l'app au premier plan (« Terminal »), ou "" si macOS ne répond pas."""
    asn = _lancer("lsappinfo", "front")
    m = re.search(r'="(.*)"', _lancer("lsappinfo", "info", "-only", "name", asn)) if asn else None
    return m.group(1) if m else ""


def _app_du_pid(pid: Any) -> str:
    """« /Applications/Cursor.app/Contents/MacOS/Cursor » → « Cursor »."""
    chemin = _lancer("ps", "-p", str(pid).strip(), "-o", "comm=")
    m = re.search(r"([^/]+)\.app/", chemin)
    return m.group(1) if m else chemin.rsplit("/", 1)[-1]


def nom_app(cible: Any) -> str:
    """L'app que vise un `app_target` de Peekaboo (« Notes », « Notes:Fenêtre », « PID:123 », « frontmost »)."""
    cible = str(cible or "").strip()
    bas = cible.lower()
    if not cible or bas.startswith(("screen", "menubar")):
        return INCONNUE
    if bas == "frontmost":
        return _app_devant() or INCONNUE
    if bas.startswith("pid:"):
        return _app_du_pid(cible[4:]) or INCONNUE
    return cible.split(":")[0].strip() or INCONNUE


def cibles_mac(action: str, entree: dict[str, Any], apps_vues: Iterable[str]) -> set[str]:
    """Les apps qui recevront ce geste."""
    if entree.get("pid"):
        return {_app_du_pid(entree["pid"]) or INCONNUE}
    if entree.get("app"):
        return {nom_app(entree["app"])}
    if action in {"paste", "menu", "dialog", "window"}:  # sans app nommée : celle du premier plan
        return {_app_devant() or INCONNUE}
    return set(apps_vues) or {INCONNUE}  # un geste sur le snapshot d'un see : les apps qu'il a regardées


def _sensible(apps: set[str]) -> bool:
    return any(a == INCONNUE or APPS_SENSIBLES.search(a) for a in apps)


def _raccourci_risque(entree: dict[str, Any]) -> bool:
    touches = [str(k) for k in entree.get("keys") or []]
    touches.append("+".join(touches))  # ["cmd", "q"] comme ["cmd+q"]
    if entree.get("key"):
        touches.append("+".join([*map(str, entree.get("modifiers") or []), str(entree["key"])]))
    for t in touches:
        morceaux = [m for m in re.split(r"[+\s]+", t.lower()) if m]
        if len(morceaux) >= 2 and {"cmd", "command", "⌘", "meta"} & set(morceaux[:-1]) \
                and morceaux[-1] in TOUCHES_RISQUE:
            return True
    return False


def atelier(action: str, entree: dict[str, Any], apps: set[str]) -> str | None:
    """L'atelier (« Cursor ») si ce geste y travaille sans rien de risqué, sinon None.
    Un ⌘Q ou un menu « Supprimer » dans Cursor reste confirmé à part."""
    if action not in GESTES_MAC or not apps or any(a == INCONNUE or not APPS_ATELIER.search(a) for a in apps):
        return None
    if action == "menu" and MENU_RISQUE.search(f"{entree.get('item', '')} {entree.get('path', '')}"):
        return None
    if action == "press" and _raccourci_risque(entree):
        return None
    return " et ".join(sorted(apps))


def _politique_mac(action: str, entree: dict[str, Any], apps_vues: Iterable[str]) -> Decision:
    quoi = entree.get("action")
    if action in {"permissions", "sleep", "scroll"}:
        return Decision.OUI
    if action == "see":  # regarder : seul, sauf dans un coffre à mots de passe
        return Decision.DEMANDER if APPS_SECRETES.search(nom_app(entree.get("app_target"))) else Decision.OUI
    if action == "clipboard":
        return Decision.OUI if quoi in {None, "status", "get"} else Decision.DEMANDER
    if action == "app":  # lister, ouvrir, masquer : seul ; quitter : il demande (travail pas enregistré)
        return Decision.OUI if quoi in {"list", "launch", "hide"} else Decision.DEMANDER
    if action == "window":
        return Decision.DEMANDER if quoi == "close" else Decision.OUI
    if action == "dialog":  # « Supprimer ? », « Autoriser l'accès ? » : toujours toi qui décides
        return Decision.OUI if quoi == "list" else Decision.DEMANDER
    if action == "menu" and quoi == "list":
        return Decision.OUI
    if action not in GESTES_MAC:  # un outil inconnu (nouvelle version de Peekaboo) : prudence
        return Decision.DEMANDER
    if _sensible(cibles_mac(action, entree, apps_vues)):
        return Decision.DEMANDER
    if action == "menu" and MENU_RISQUE.search(f"{entree.get('item', '')} {entree.get('path', '')}"):
        return Decision.DEMANDER
    if action == "press" and _raccourci_risque(entree):
        return Decision.DEMANDER
    return Decision.OUI


def _dans(apps: Iterable[str]) -> str:
    noms = sorted(a for a in apps if a != INCONNUE)
    return f"dans {' et '.join(noms)}" if noms else "dans une fenêtre que je n'ai pas identifiée"


def _court(texte: Any, n: int = 100) -> str:
    texte = str(texte or "").strip()
    return texte if len(texte) <= n else texte[:n].rsplit(" ", 1)[0] + "…"


def _decrire_mac(action: str, entree: dict[str, Any], apps: set[str]) -> str:
    quoi = entree.get("action")
    if action == "app":
        qui = entree.get("name") or entree.get("bundleId") or ("toutes tes apps" if entree.get("all") else "cette app")
        return f"Je vais quitter {qui}. Je peux ?"
    if action == "window":
        return f"Je vais fermer une fenêtre {_dans(apps)}. Je peux ?"
    if action == "dialog":
        bouton = entree.get("button")
        geste = f"cliquer « {bouton} »" if bouton else "répondre à une fenêtre de dialogue"
        return f"Je vais {geste} {_dans(apps)}. Je peux ?"
    if action == "clipboard":
        return "Je vais enregistrer ton presse-papiers dans un fichier. Je peux ?"
    if action == "see":
        return f"Je vais regarder {_dans({nom_app(entree.get('app_target'))})}, là où sont tes mots de passe. Je peux ?"
    if action in {"type", "paste"}:
        return f"Je vais écrire « {_court(entree.get('text'))} » {_dans(apps)}. Je peux ?"
    if action == "press":
        touches = entree.get("keys") or ["+".join([*map(str, entree.get("modifiers") or []), str(entree.get("key", ""))])]
        return f"Je vais appuyer sur {', '.join(map(str, touches))} {_dans(apps)}. Je peux ?"
    if action == "menu":
        return f"Je vais choisir le menu « {entree.get('item') or entree.get('path')} » {_dans(apps)}. Je peux ?"
    return f"Je vais cliquer {_dans(apps)}. Je peux ?"


def _decrire_atelier(app: str, action: str, entree: dict[str, Any]) -> str:
    """La question unique avant de travailler dans Cursor : si le premier geste écrit, il lit le texte."""
    texte = f" J'écris : « {_court(entree.get('text'), 160)} »." if action in {"type", "paste"} else ""
    return f"Je prends la main sur {app} le temps de cette demande.{texte} Je peux ?"


def _decrire_zapier(entree: dict[str, Any]) -> str:
    """« SlackCLIAPI@1.3 », action « direct_message » → « Je vais utiliser Slack, action direct message : « … » »."""
    app = re.sub(r"(V\d+)?(CLI)?API$", "", str(entree.get("selected_api", "")).split("@")[0])
    app = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", app) or "une app"
    nom = str(entree.get("action", "")).replace("_", " ")
    params = entree.get("params") or {}
    details = [_court(v, 80) for k, v in params.items()
               if isinstance(v, str) and k.lower() in DETAILS_ZAPIER and v.strip()][:2]
    suite = f" : « {' », « '.join(details)} »" if details else ""
    return f"Je vais utiliser {app} par Zapier, action {nom}{suite}. Je peux ?"


DETAILS_ZAPIER = {"text", "message", "body", "subject", "title", "name", "channel", "to", "email", "content"}


def decrire(outil: str, entree: dict[str, Any], apps: Iterable[str] = ()) -> str:
    """Formule la question de confirmation, à l'oral."""
    if outil == "Bash":
        quoi = entree.get("description") or "lancer une commande sur ton Mac"
        return f"Je vais {quoi[0].lower() + quoi[1:]}. Je peux ?"
    if outil in ECRITURE_FICHIERS:
        dossier = Path(str(entree.get("file_path") or "")).parent
        return f"Je vais modifier des fichiers dans le dossier {dossier.name or dossier}. Je peux ?"
    if outil in LECTURE_FICHIERS:
        return f"Je vais lire {Path(chemin_fichier(entree).split()[0]).name}, qui peut contenir des secrets. Je peux ?"
    action = nom_action(outil)
    if action == "noter_lecon":
        return f"Je note dans mon carnet : « {str(entree.get('lecon', '')).strip()} »{_a_la_place(entree)}. Je peux ?"
    if action == "noter_mot":
        return (f"Je note dans mon lexique : quand j'entends « {str(entree.get('entendu', '')).strip()} », tu veux dire "
                f"« {str(entree.get('voulu', '')).strip()} ». Je peux ?")
    if action == "noter_savoir_faire":
        titre, quand = str(entree.get('titre', '')).strip(), ' '.join(str(entree.get('quand') or '').split())
        return (f"J'écris ma recette « {titre} »" + (f" (quand : {quand})" if quand else "")
                + f" : {' '.join(str(entree.get('etapes', '')).split())}. Je peux ?")
    if action == "noter_publication":
        return (f"Je note que {str(entree.get('projet', '')).strip()} est sur {str(entree.get('site', '')).strip()} : "
                f"« {str(entree.get('resume', '')).strip()} » (lien : {str(entree.get('lien', '')).strip() or 'aucun'}). Je peux ?")
    if action == "retenir":
        return f"Je retiens sur toi : « {str(entree.get('fait', '')).strip()} »{_a_la_place(entree)}. Je peux ?"
    if action == "oublier":
        return f"J'efface de ma mémoire ce qui parle de « {str(entree.get('texte', '')).strip()} ». Je peux ?"
    if action == "programmer_annonce":
        # Ce qui sera vraiment gardé (la consigne `faire` l'emporte sur la phrase `dire`), et quand, répétition comprise.
        quoi = (f"je ferai « {entree['faire']} »" if entree.get("faire") else f"je dirai « {entree.get('dire') or ''} »")
        quand = " ".join(str(entree.get("quand") or "plus tard").split())[:20]
        return f"Je programme {'chaque jour à partir de' if entree.get('chaque_jour') else 'pour'} {quand} : {quoi}. Je peux ?"
    if action == "lancer_tache_de_fond":
        return f"Je confie à un agent « {entree.get('titre') or ''} » : {entree.get('brief') or ''}. Je peux ?"
    if action == "demander_accord":
        return f"{entree.get('action', 'Je continue').strip().rstrip('.')}. Je peux ?"
    if action in {"creer_mot_de_passe", "copier_mot_de_passe"}:
        # Le domaine que l'outil utilisera vraiment (« https://www.Canva.com/x » → « canva.com »), pas le texte brut.
        site = comptes.domaine(entree.get("site")) or "une adresse qui n'est pas un site"
        if action == "copier_mot_de_passe":
            return f"Je sors ton mot de passe de {site} du trousseau pour me connecter. Je peux ?"
        qui = comptes.identifiant_valide(entree.get("identifiant")) or "un identifiant invalide"
        return f"Je crée ton compte sur {site} avec {qui}, et je range son mot de passe dans ton trousseau. Je peux ?"
    if action == "chercher_code_recu":
        return "Je regarde le code que tu viens de recevoir par SMS. Je peux ?"
    if outil.startswith("mcp__mac__"):
        return _decrire_mac(action, entree, set(apps))
    if outil.startswith(CHROME_PERSO):
        if action in {"file_upload", "upload_image"}:
            return "Je dois envoyer un fichier de ton Mac sur ce site, dans ton Chrome. Je peux ?"
        return "Je dois lancer un bout de code dans ta page Chrome. Je peux ?"
    if outil.startswith("mcp__navigateur__"):
        if action.startswith(("browser_run_code", "browser_evaluate")):
            return "Je dois lancer un bout de code dans la page. Je peux ?"
        if action == "browser_file_upload":
            return "Je dois envoyer un fichier de ton Mac sur ce site. Je peux ?"
        return "Je dois toucher aux cookies ou à la session de ce site. Je peux ?"
    if action == "mettre_a_la_corbeille":
        noms = [Path(c).name or c for c in chemins_a_jeter(entree)]
        quoi = " et ".join(noms) if len(noms) <= 2 else f"{len(noms)} éléments, dont {noms[0]}"
        return f"Je vais mettre à la corbeille {quoi or 'ce fichier'}. Je peux ?"
    if action == "effacer_memoire":
        quoi = {"memoire": "ce que je sais de toi", "conversations": "l'historique de nos conversations"}.get(
            str(entree.get("quoi") or ""), "ce que je sais de toi et nos conversations")
        return f"Je vais effacer {quoi}. Je peux ?"
    if action == "envoyer_imessage":
        qui = entree.get("nom") or entree.get("destinataire", "ce contact")
        return f"J'envoie à {qui} : « {entree.get('texte', '')} ». Je peux ?"
    if action == "appeler":
        numero = appels.numero_appelable(entree.get("numero")) or str(entree.get("numero", ""))
        if urgence := appels.numero_urgence(numero):
            return f"J'appelle le {appels.national(numero)}, {urgence}. Je peux ?"
        qui = entree.get("nom")
        return f"J'appelle {qui} au {numero}. Je peux ?" if qui else f"J'appelle le {numero}. Je peux ?"
    if action == "execute_zapier_write_action":
        return _decrire_zapier(entree)
    if action == "share_file":
        qui = entree.get("emailAddress") or ("tout le monde qui a le lien"
                                             if entree.get("permissionId") == "anyoneWithLink" else "ce groupe")
        droit = {"writer": "en modification", "commenter": "en commentaire"}.get(entree.get("role"), "en lecture")
        return f"Je vais partager ce fichier avec {qui}, {droit}. Je peux ?"
    if action in ACTIONS:
        return f"Je vais {ACTIONS[action]}. Je peux ?"
    nom = action.replace("_", " ")
    service = outil.split("__")[1].removeprefix("claude_ai_").replace("_", " ") if outil.startswith("mcp__") else outil
    return f"Je dois utiliser {service}, action {nom}. Je peux ?"


ACTIONS = {
    "send_message": "envoyer ce mail",
    "reply": "envoyer cette réponse",
    "forward": "transférer ce mail",
    "create_draft": "préparer ce brouillon",
    "trash_message": "mettre ces mails à la corbeille",
    "trash_thread": "mettre ces mails à la corbeille",
    "create_event": "ajouter ce rendez-vous à ton agenda",
    "update_event": "modifier ce rendez-vous",
    "delete_event": "supprimer ce rendez-vous",
    "respond_to_event": "répondre à cette invitation",
    # Google Drive
    "create_file": "créer ce fichier dans ton Drive",
    "update_file": "modifier ce fichier de ton Drive",
    "copy_file": "copier ce fichier dans ton Drive",
    "trash_file": "mettre ce fichier de ton Drive à la corbeille",
    "request_access": "demander l'accès à ce fichier, son propriétaire recevra un mail",
    # Notion
    "create_pages": "créer cette page dans ton Notion",
    "update_page": "modifier cette page de ton Notion",
    "move_pages": "déplacer ces pages de ton Notion",
    "duplicate_page": "dupliquer cette page de ton Notion",
    "create_database": "créer cette base dans ton Notion",
    "update_database": "modifier cette base de ton Notion",
    "create_comment": "laisser ce commentaire dans ton Notion",
    # Zapier
    "enable_zapier_action": "ajouter une action à ton Zapier",
    "disable_zapier_action": "retirer une action de ton Zapier",
}
# Ce qui part chez quelqu'un d'autre : confirmé à chaque fois, même s'il y en a plusieurs d'affilée.
PAR_APPEL = {"send_message", "reply", "forward", "share_file", "execute_zapier_write_action", "request_access"}


def motif_interdit(outil: str, entree: dict[str, Any]) -> str:
    """Pourquoi le gardien refuse d'office une commande Bash, et ce que Steve doit faire à la place ("" sinon)."""
    if outil != "Bash":
        return ""
    commande = str(entree.get("command", "")).strip()
    if INTERDIT.search(commande):
        return ("Le gardien interdit cette commande (sudo, rm -rf, formater un disque, éteindre ou redémarrer le "
                "Mac). Ne la contourne pas : dis-lui en une phrase que tu ne peux pas le faire d'ici.")
    if SUPPRESSION_DIRECTE.search(commande):
        return ("Le gardien interdit de supprimer avec rm, rmdir, unlink ou find -delete. Utilise l'outil "
                "mettre_a_la_corbeille avec les chemins complets : la corbeille se rattrape.")
    return ""


# Tant qu'un mot de passe de comptes.py peut être dans le presse-papiers, il ne peut que cliquer, taper et coller :
# tout le reste attend (commande, code, capture, photo de la page, raccourci…). Une liste de ce qui est permis, pas
# de ce qui est interdit : un nouvel outil qui lirait le presse-papiers est fermé d'office. Cela dure au plus
# PRESSE_PAPIERS_S, et quinze secondes après le dernier collage (cmd+v).
GESTES_PENDANT_COLLAGE = {"browser_click", "browser_press_key", "browser_type", "browser_hover", "browser_wait_for",
                          "click", "type", "press", "hotkey", "scroll", "form_input",
                          "creer_mot_de_passe", "copier_mot_de_passe", "demander_accord", "signaler_captcha"}
GESTES_CHROME = {"left_click", "double_click", "triple_click", "key", "type", "scroll", "wait", "hover"}
_COLLER = re.compile(r"\b(meta|cmd|command|control|ctrl)\s*\+\s*v\b", re.IGNORECASE)


def lit_le_presse_papiers(outil: str, entree: dict[str, Any]) -> bool:
    """Vrai pour tout ce qui n'est pas un geste : ce qui pourrait voir le presse-papiers, ou la page où il est collé."""
    action = nom_action(outil)
    if action == "computer":   # l'outil de son Chrome : ses gestes oui, sa capture d'écran non
        return entree.get("action") not in GESTES_CHROME
    return action not in GESTES_PENDANT_COLLAGE


def colle(outil: str, entree: dict[str, Any]) -> bool:
    return any(m in nom_action(outil) for m in ("press_key", "key", "hotkey", "computer")) \
        and bool(_COLLER.search(str(entree)))


# --- Payer -----------------------------------------------------------------------------------------------------
# Un numéro de carte n'a rien à faire dans ses outils (le remplissage automatique de Chrome s'en charge) : il est
# refusé partout, qu'il le tape, l'écrive dans un fichier ou le passe à une commande. Seuls les formats de vraies
# cartes comptent (Visa, Mastercard, CB, Amex, Discover) et le chiffre de contrôle (Luhn) doit tomber juste : un
# SIRET (14 chiffres), une date en millisecondes (13) ou un numéro de colis ne sont pas pris pour une carte.
# Entre les groupes : n'importe quel séparateur (espace, espace insécable, point, tiret, barre), pas seulement « ».
_SEPARATEUR = r"[\s.\-/_·•]{1,3}"
_SUITE_DE_CHIFFRES = re.compile(rf"(?<!\d)\d+(?:{_SEPARATEUR}\d+)*(?!\d)")
# Un champ de carte (numéro, cryptogramme, date d'expiration) : il n'y tape rien, même chiffre par chiffre.
CHAMP_CARTE = re.compile(
    r"card ?number|num[ée]ro (?:de (?:la )?)?carte|\bcc-?(?:number|num|csc|exp)|cardnumber|\bcvc\b|\bcvv\b|\bcsc\b"
    r"|cryptogramme|code de s[ée]curit[ée]|security code|date d'expiration|expiration date|expiry",
    re.IGNORECASE,
)
FRAPPES = {"browser_type", "browser_fill_form", "fill_form", "form_input", "type", "set_value"}
_FORMATS_CARTE = {15: re.compile(r"3[47]"), 16: re.compile(r"4|5[1-5]|2[2-7]|6[05]"), 19: re.compile(r"4|6[05]")}


def luhn(chiffres: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(chiffres)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def numero_de_carte(texte: str) -> bool:
    """Vrai si le texte contient un numéro de carte bancaire (« 4970 1012 3456 7890 », « 4970-… », collé)."""
    for suite in _SUITE_DE_CHIFFRES.finditer(texte):
        groupes = re.split(_SEPARATEUR, suite.group())
        for debut in range(len(groupes)):   # la carte peut être entourée d'autres nombres (« 4970 … 7890 12/28 »)
            chiffres = ""
            for g in groupes[debut:]:
                chiffres += g
                if len(chiffres) > 19:
                    break
                motif = _FORMATS_CARTE.get(len(chiffres))
                if motif and motif.match(chiffres) and luhn(chiffres):
                    return True
    return False


# Quand config.PAIEMENTS est à « non » (par défaut) : les boutons et les outils qui paient lui sont refusés. Le
# filet attrape les clics nommés (Playwright, Peekaboo, une boîte de dialogue) ; un clic à des coordonnées n'a pas
# de nom, c'est sa consigne (« ne paie jamais ») qui l'arrête.
BOUTON_QUI_PAIE = re.compile(
    r"\b(?:payer|pay|paiement|payment|paypal|acheter|achat|buy|purchase|commander|passer (?:la |ma )?commande"
    r"|place (?:your |my )?order|order now|checkout|s'abonner|s’abonner|abonnez-vous|subscribe"
    r"|souscrire|apple pay|google pay|confirmer (?:et payer|le paiement|la commande)"
    r"|valider (?:et payer|le paiement|la commande|mon panier))\b",
    re.IGNORECASE,
)
ACTION_QUI_PAIE = re.compile(
    r"(?:(?<![a-zA-Z])|(?<=[a-z])(?=[A-Z]))(?i:pay|payment|purchase|checkout|buy|charge|subscribe|subscription)s?(?![a-z])")
CLICS_NOMMES = {"browser_click", "click", "dialog", "menu", "action"}


def _textes(valeur: Any):
    """Chaque texte d'une entrée, tel quel (pas sa forme repr, où une espace insécable devient « \\xa0 »)."""
    if isinstance(valeur, dict):
        for v in valeur.values():
            yield from _textes(v)
    elif isinstance(valeur, (list, tuple)):
        for v in valeur:
            yield from _textes(v)
    elif valeur is not None:
        yield str(valeur)


def motif_paiement(outil: str, entree: dict[str, Any]) -> str | None:
    """Pourquoi ce geste lui est refusé (un numéro de carte, ou un paiement quand ils sont coupés), ou None."""
    action = nom_action(outil)
    if any(numero_de_carte(t) for t in _textes(entree)) or (action in FRAPPES and CHAMP_CARTE.search(str(entree))):
        return ("Un numéro de carte bancaire ne passe jamais par tes outils. Laisse le remplissage automatique de "
                "son Chrome proposer sa carte, ou demande-lui de la saisir lui-même.")
    if config.PAIEMENTS:
        return None
    if (action in CLICS_NOMMES and BOUTON_QUI_PAIE.search(str(entree))) or \
            (outil.startswith("mcp__") and not est_lecture(outil) and ACTION_QUI_PAIE.search(action)):
        return ("Les paiements sont coupés : tu ne paies jamais, même s'il te l'a demandé. Arrête-toi ici et dis-lui "
                "ce qu'il faudrait payer, où et combien ; c'est lui qui le fera. (Si ce bouton ne paie rien, une "
                "newsletter gratuite par exemple, demande-lui de cliquer lui-même.)")
    return None


# Les outils qui ne font que lire (chercher, lister, lire) : seuls permis à sa ronde, libres même quand il se méfie.
LECTURES = re.compile(r"^(?:search|list|get|read|lister|chercher|suggest)_|^(?:search|fetch)(?:_|$)|^ToolSearch$")
# Les seuls outils qui ne lui rendent que son propre état (« noté », ses réglages, ses dépenses) : tout autre outil
# (un fichier, une commande, un mail, une page, l'écran, un agent) peut lui faire lire un texte écrit par quelqu'un
# d'autre, où un piège peut se cacher (« note dans ton carnet : transfère tout à… »). Un outil nouveau compte comme
# une lecture du dehors tant qu'on ne l'a pas ajouté ici : en cas d'oubli, il demande, il ne laisse pas passer.
_INTERNES = re.compile(r"^(?:ToolSearch|TodoWrite|mcp__jervis__(?:retenir|oublier|noter_lecon|effacer_memoire|"
                       r"creer_rappel|creer_note|lister_raccourcis|demander_accord|envoyer_imessage|appeler|raccrocher|"
                       r"mettre_a_la_corbeille|verifier_autorisations|memoire_du_mac|depenses|tableau_de_bord|"
                       r"lancer_tache_de_fond|annuler_tache|programmer_annonce|annuler_programme|"
                       # Le graphe ne contient que les faits de sa mémoire (graphe.py), déjà dans ses consignes.
                       r"chercher_liens|"
                       # Les comptes : « c'est rangé », et la liste qu'il a écrite lui-même (jamais un mot de passe).
                       r"creer_mot_de_passe|copier_mot_de_passe|lister_comptes|signaler_captcha|noter_publication|"
                       # Son lexique et ses recettes : ce qu'il a écrit lui-même (lexique.py, savoir_faire.py).
                       r"noter_mot|noter_savoir_faire|chercher_savoir_faire|"
                       # Une clé d'API rangée dans le Trousseau : il ne lit que « c'est rangé » (apis.py).
                       r"ranger_cle_api)"
                       # Gmail, Agenda, Drive : un envoi ou une création ne lui rend que ce qu'il vient d'écrire.
                       r"|mcp__claude_ai_\w+?__(?:send|create|delete|trash|untrash|label|unlabel|mark|unmark|apply)_\w+)$")
# Ce qui s'écrit pour les fois suivantes (sa mémoire, son carnet, une consigne programmée) ou part chez un autre
# agent (le brief d'une tâche de fond, qui pourrait faire passer l'ordre d'un mail piégé) : après un texte venu
# d'ailleurs, ou pendant le compte rendu d'un agent, c'est toi qui dis oui, en voyant le texte exact.
ECRIT_POUR_PLUS_TARD = {"retenir", "oublier", "noter_lecon", "programmer_annonce", "lancer_tache_de_fond",
                         "noter_publication", "noter_mot", "noter_savoir_faire"}
# Ce qu'il te lit de ces outils avant de dire oui, et jusqu'où. Ce qui s'exécute est ce texte-là, mot pour mot :
# au-delà de la limite, il ne coupe pas en douce, il refuse et redit plus court.
A_MONTRER: dict[str, dict[str, int]] = {
    "retenir": {"fait": 200, "remplace": 80, "categorie": 40},
    "oublier": {"texte": 100},
    "noter_lecon": {"lecon": 200, "remplace": 80, "domaine": 40},
    "programmer_annonce": {"faire": 200, "dire": 200, "quand": 20},
    "lancer_tache_de_fond": {"titre": 60, "brief": 500},
    "noter_publication": {"projet": 60, "site": 100, "lien": 300, "resume": 200},
    "noter_mot": {"entendu": 40, "voulu": 40},
    "noter_savoir_faire": {"titre": 80, "quand": 200, "etapes": 1500},
}


def _a_la_place(entree: dict[str, Any]) -> str:
    """« , à la place de ce qui parle de … » : ce qu'il effacera en même temps (retenir, noter_lecon)."""
    return f", à la place de ce qui parle de « {entree['remplace']} »" if entree.get("remplace") else ""


def tel_que_montre(action: str, entree: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """L'entrée telle qu'il te la montre (espaces et retours à la ligne ramenés à un espace ; une consigne `faire`
    l'emporte sur la phrase `dire`, comme au programmateur), et le champ trop long pour être montré ("" sinon)."""
    net = dict(entree)
    if action == "programmer_annonce" and net.get("faire"):
        net.pop("dire", None)
    for champ, limite in A_MONTRER.get(action, {}).items():
        if isinstance(net.get(champ), str):
            net[champ] = " ".join(net[champ].split())
            if len(net[champ]) > limite:
                return net, champ
    return net, ""


def est_lecture(outil: str) -> bool:
    return bool(LECTURES.search(nom_action(outil)))


def lit_dehors(outil: str) -> bool:
    """Vrai si l'outil peut lui faire lire un texte venu d'ailleurs (tous, sauf ceux de _INTERNES)."""
    return not _INTERNES.match(outil)


# Quand lui (ou un agent de fond) a lu un texte venu d'ailleurs pour la dernière fois : un iMessage de « toi » qui
# arrive juste après a pu être écrit par lui, sous la dictée d'une page piégée (distance.py le fait confirmer).
_lu_dehors_a = float("-inf")


def noter_lecture_dehors() -> None:
    global _lu_dehors_a
    _lu_dehors_a = time.monotonic()


def lu_dehors_depuis() -> float:
    """Secondes depuis le dernier texte venu d'ailleurs (l'infini s'il n'en a jamais lu)."""
    return time.monotonic() - _lu_dehors_a


# Écrire dans Messages autrement que par envoyer_imessage : refusé. Ce que l'outil envoie est noté
# (distance.noter_envoi) et n'est jamais relu comme un ordre de toi ; un script ou un geste à l'écran, si : c'est la
# porte par laquelle une page piégée se ferait passer pour toi dans ta conversation avec toi-même. Toute mention de
# l'app dans un script (AppleScript, JXA, Python/ScriptingBridge), ses identifiants, ses adresses imessage:/sms:.
_MESSAGES_PAR_SCRIPT = re.compile(
    r"""\bcom\.apple\.(?:iChat|MobileSMS|Messages)\b"""
    r"""|\bapp(?:lication)?\s*(?:id\s+)?\(?\s*["']?Messages\b"""
    r"""|\b(?:SBApplication|NSRunningApplication|NSWorkspace)\b[^\n]*\bMessages\b"""
    r"""|\b(?:imessage|sms|messages)://|(?:^|[\s"'(=])(?:imessage|sms):""",
    re.IGNORECASE)
# Ce qui pilote d'autres apps (des frappes, des clics, des scripts d'app) : après un texte venu d'ailleurs, toujours
# demandé, quel que soit le mode. Un « keystroke » dans la fenêtre de Messages enverrait un faux toi.
PILOTE_APPS = re.compile(
    r"\bosascript\b|\bshortcuts\s+run\b|\bautomator\b|\bcliclick\b|System Events|\bkeystroke\b|\bkey code\b"
    r"|ScriptingBridge|SBApplication|NSAppleScript|OSAKit|\bappscript\b|CGEvent|pyautogui|pynput|AXUIElement",
    re.IGNORECASE)
_ECRIT_DU_CODE = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def _texte_de(entree: Any) -> str:
    """Tout le texte d'une entrée d'outil, à plat (les valeurs, pas les clés)."""
    if isinstance(entree, dict):
        return " ".join(_texte_de(v) for v in entree.values())
    if isinstance(entree, (list, tuple)):
        return " ".join(_texte_de(v) for v in entree)
    return str(entree) if entree is not None else ""


def envoie_par_messages(outil: str, entree: dict[str, Any]) -> bool:
    return outil in _ECRIT_DU_CODE and bool(_MESSAGES_PAR_SCRIPT.search(_texte_de(entree)))


# Les seules commandes qui touchent à d'autres apps sans question après une page lue : le volume, Spotify ou Music,
# ouvrir une adresse web ou lancer une app. La commande entière, d'un seul morceau : « osascript -e 'set volume 5'
# -e '…keystroke…' » n'en est pas une.
_ANODIN = re.compile(
    r"""osascript\s+-e\s+(['"])(?:set\s+volume\s+(?:output\s+volume\s+)?\d{1,3}|set\s+volume\s+output\s+muted\s+"""
    r"""(?:true|false)|output\s+volume\s+of\s+\(get\s+volume\s+settings\)|tell\s+application\s+"(?:Spotify|Music)"\s+"""
    r"""to\s+(?:play|pause|playpause|next\s+track|previous\s+track))\1"""
    # Une adresse entre guillemets peut porter « & » ou « ; » (ils restent dans l'adresse) ; sans guillemets, non.
    r"""|open\s+(?:-a\s+(?:"[\w .\-]+"|'[\w .\-]+'|[\w.\-]+)\s*)?"""
    r"""(?:"https?://[^\s"`$\\]+"|'https?://[^\s']+'|https?://[^\s"'`$;&|<>\\()]+)?""",
    re.IGNORECASE)
# « open » sur autre chose (un fichier, une app avec un document) : ce que macOS lance là ne passe pas par la cage.
_OUVRE = re.compile(_COMMANDE + r"open\b(?!\s*\()")  # pas le open( de Python


_OUVRE_APP = re.compile(r"""(open\s+-a\s+)("[\w .\-]+"|'[\w .\-]+'|[\w.\-]+)""", re.IGNORECASE)
# Où « open -a » cherche une app sans question : les dossiers d'apps du système. Une app ailleurs (~/Downloads,
# ~/Applications…) a pu être posée par un script : LaunchServices la trouverait par son nom et la lancerait hors cage.
_DOSSIERS_APPS = (Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
                  Path("/System/Applications/Utilities"))


def app_du_systeme(nom: str) -> Path | None:
    """Le chemin d'une app installée dans /Applications ou /System/Applications, d'après son nom ; sinon None."""
    nom = nom.strip("\"'")
    nom = nom[:-4] if nom.lower().endswith(".app") else nom
    if not nom or nom.startswith(".") or "/" in nom:
        return None
    voulu = f"{nom}.app".casefold()
    for dossier in _DOSSIERS_APPS:
        try:
            # le vrai nom sur le disque, pas celui de la commande : APFS ouvrirait Calculator.app pour « calculator »
            vrai = next((e.name for e in os.scandir(dossier) if e.name.casefold() == voulu), None)
        except OSError:
            continue
        if vrai and (app := dossier / vrai).is_dir() and not app.is_symlink():
            return app
    return None


def avec_chemin_app(commande: str) -> str:
    """« open -a Spotify » → « open -a '/Applications/Spotify.app' » : c'est cette app-là qui s'ouvre, pas une
    homonyme posée ailleurs que LaunchServices préférerait."""
    m = _OUVRE_APP.search(commande)
    if not m or not (app := app_du_systeme(m.group(2))):
        return commande
    return commande[:m.start(2)] + shlex.quote(str(app)) + commande[m.end(2):]


def commande_anodine(commande: str) -> bool:
    commande = commande.strip()
    if not commande or not _ANODIN.fullmatch(commande):
        return False
    m = _OUVRE_APP.search(commande)  # lancer une app : seulement une app du système, d'après son nom
    return not m or app_du_systeme(m.group(2)) is not None


def pilote_des_apps(outil: str, entree: dict[str, Any]) -> bool:
    if outil not in _ECRIT_DU_CODE:
        return False
    if outil == "Bash":
        commande = str(entree.get("command") or "")
        if commande_anodine(commande):
            return False  # le volume, Spotify, une adresse web : la courte liste de toujours
        return bool(PILOTE_APPS.search(commande) or _OUVRE.search(commande))
    return bool(PILOTE_APPS.search(_texte_de(entree)))


# Après un texte venu d'ailleurs, chaque commande que le Gardien laisse passer seule tourne dans une cage de macOS
# (sandbox-exec) : un script qu'une page aurait dicté, même maquillé (base64, eval, un fichier écrit avant, lancé
# en fond), ne peut ni piloter une app (Apple Events, frappes, clics, lancer une app), ni écrire dans Messages, ni
# s'installer pour plus tard (LaunchAgents, cron, ton shell, le code de Steve, Claude Code). Le reste marche comme
# avant : fichiers, réseau, Python, son. Ce qu'il fait dans la cage, ses enfants le font aussi.
CAGE_EXEC = "/usr/bin/sandbox-exec"
_CAGE_INTERDITS = ("/usr/bin/open", "/usr/bin/osascript", "/bin/launchctl", "/usr/bin/crontab", "/usr/bin/at",
                   "/usr/bin/batch", "/usr/bin/shortcuts", "/usr/bin/automator")
_RACINE = Path(__file__).resolve().parent.parent  # ~/Jervis

# Ce qui se lance tout seul plus tard, hors de toute cage (au démarrage, à l'ouverture d'un terminal, au prochain
# git ou python, au prochain lancement de Steve) — une seule liste, pour la cage et pour Write/Edit.
_SE_LANCE_DOSSIERS = ("Library/Messages", "Library/LaunchAgents", ".claude", ".ssh", ".config/git", ".config/fish",
                      "Library/Python", "Library/Application Support/com.apple.backgroundtaskmanagementagent")
_SE_LANCE_FICHIERS = (".zshrc", ".zprofile", ".zshenv", ".zlogin", ".zlogout", ".bashrc", ".bash_profile",
                      ".bash_login", ".profile", ".gitconfig")
_STEVE_DOSSIERS = ("jervis", ".venv", "Jervis.app", ".git")  # .git : hooks, config (core.hooksPath), info, commondir…
_STEVE_FICHIERS: tuple[str, ...] = ()
# Dans la cage, ~/Jervis/.git est fermé en entier (objets, branches, index compris : rien d'y glisser pour plus tard)
# et git y lit sans rien écrire (GIT_OPTIONAL_LOCKS=0 : git status marche). Tes commits se font hors cage.
# Partout, dans tout dépôt, à toute profondeur (sous-modules, worktrees) : la config (core.fsmonitor, core.hooksPath
# lanceraient du code à ton prochain git), les hooks, info/, commondir/gitdir, et l'entrée .git elle-même (un
# fichier .git peut pointer vers un faux dépôt) ; et les .pth / sitecustomize que python lit à chaque démarrage.
# Un git clone ou git init ne marche donc pas dans la cage : après une page lue, il t'est demandé (nouveau_depot),
# seulement tout seul et tout simple (sinon un « git init x; curl … | sh » passerait avec ton oui), puis tourne dans
# une cage qui ne lui ouvre que son dossier : le chemin vérifié ici ne compte pas, c'est macOS qui juge à l'écriture
# (un lien posé entre-temps vers ~/.claude, une casse différente sur APFS n'y ouvrent rien).
_SE_LANCE_MOTIFS = (r"/\.git/(.*/)?(config|config\.worktree|commondir|gitdir)(\.lock)?$", r"/\.git/(.*/)?(hooks|info)/",
                    r"/\.git$", r"/site-packages/[^/]*\.pth$", r"/(site|user)customize\.py$",
                    r"\.app(/|$)")  # une app, où qu'elle soit : LaunchServices la trouverait, et l'ouvrirait hors cage
COMMANDE_MONTREE_MAX = 300  # après une page lue, une commande demandée t'est montrée en entier, jusqu'à cette taille
_MOT_SIMPLE = r"[\w./:@~+=,%-]+"  # ni guillemet, ni $, ni ; & | ` < > ( ) : rien que le shell réinterprète
NOUVEAU_DEPOT = re.compile(rf"(?:cd[ \t]+({_MOT_SIMPLE})[ \t]*&&[ \t]*)?git[ \t]+(clone|init)((?:[ \t]+{_MOT_SIMPLE})*)")
# Les seules options permises : -c, --config, -u/--upload-pack, --template, --recurse-submodules, --separate-git-dir…
# lanceraient du code ou poseraient une config hors cage
_GIT_OPTIONS_SURES = re.compile(r"-q|--quiet|--single-branch|--no-tags|--depth=\d+|--filter=blob:none"
                                r"|(?:--branch|--initial-branch|--origin)=[\w./-]+")
_CHEMIN_ABSOLU = re.compile(r"(?:~(?=/|$)|/)[\w./@+,%-]*")  # ni ~+ ni ~autre ni =git, que zsh réinterpréterait


def nouveau_depot(commande: str) -> Path | None:
    """Le dossier d'un git clone ou git init tout seul et tout simple, vers un chemin donné en entier (un clone sans
    dossier, ou relatif sans « cd /chemin && » devant, tomberait là où un cd d'avant l'a mis), qui n'est ni dans ce qui
    se lance tout seul (~/.claude, LaunchAgents, le code de Steve…), ni au-dessus, ni dans un .git. Sinon None."""
    m = NOUVEAU_DEPOT.fullmatch(commande.strip())
    if not m:
        return None
    dossier, verbe, mots = m.group(1), m.group(2), m.group(3).split()
    for mot in mots:
        if mot.startswith("-") and not _GIT_OPTIONS_SURES.fullmatch(mot):
            return None
        if not mot.startswith("-") and "::" in mot:  # ext::sh -c … : un transport qui lance une commande
            return None
    noms = [mot for mot in mots if not mot.startswith("-")]
    attendus = 2 if verbe == "clone" else 1
    if len(noms) != attendus and not (verbe == "init" and not noms and dossier):
        return None  # le dossier d'un clone, on ne le devine pas d'après l'URL : il est écrit dans la commande
    cible = noms[-1] if len(noms) == attendus else ""
    try:
        if _CHEMIN_ABSOLU.fullmatch(cible):
            ou = Path(cible).expanduser().resolve()
        elif dossier and _CHEMIN_ABSOLU.fullmatch(dossier) and not cible.startswith(("~", "=")):
            ou = (Path(dossier).expanduser() / cible).resolve()
        else:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    if (any(partie.casefold() == ".git" for partie in ou.parts) or SE_LANCE_PLUS_TARD.search(f"{ou}/x")
            or SE_LANCE_PLUS_TARD.search(_plie(f"{ou}/x"))):
        return None
    ici = str(ou).casefold()  # APFS ne distingue pas ~/.Claude de ~/.claude
    for c in (*_se_lance_chemins()[0], *_se_lance_chemins()[1]):
        if str(c).casefold() == ici or str(c).casefold().startswith(ici.rstrip("/") + "/"):
            return None  # ni ~ ni ~/Jervis : ils contiennent du protégé
    if ou.is_symlink() or (ou.exists() and (not ou.is_dir() or any(ou.iterdir()))):
        return None  # un dossier neuf ou vide : le dossier ouvert ne couvre jamais ce qui était déjà là
    return ou


def _se_lance_chemins() -> tuple[list[Path], list[Path]]:
    maison = Path.home().resolve()
    return ([maison / d for d in _SE_LANCE_DOSSIERS] + [_RACINE / d for d in _STEVE_DOSSIERS],
            [maison / f for f in _SE_LANCE_FICHIERS] + [_RACINE / f for f in _STEVE_FICHIERS])


def _sbpl(chemin: Path) -> str:
    """Un chemin dans une chaîne du profil (un nom de dossier peut contenir « " » ou « \\ »)."""
    return str(chemin).replace("\\", "\\\\").replace('"', '\\"')


# Ce qu'APFS confond avec des lettres ASCII, au-delà de la casse (pliage Unicode) : K (kelvin) avec k, ſ avec s, et
# les ligatures ﬁ, ﬆ… avec fi, st. Un « hooKs » ou un « conﬁg » ouvrirait le même fichier que hooks ou config.
_PLIAGES = {"s": ("\u017f",), "k": ("\u212a",), "ss": ("\u00df", "\u1e9e"), "ff": ("\ufb00",), "fi": ("\ufb01",),
            "fl": ("\ufb02",), "ffi": ("\ufb03",), "ffl": ("\ufb04",), "st": ("\ufb05", "\ufb06")}


def _mot_sans_casse(mot: str) -> str:
    """Une suite de lettres ASCII, dans toutes les façons de l'écrire qu'APFS confond avec elle."""
    if not mot:
        return ""
    lettre = mot[0]
    autres = _PLIAGES.get(lettre.lower(), ())
    debut = f"({'|'.join((lettre.lower(), lettre.upper(), *autres))})" if autres else f"[{lettre.lower()}{lettre.upper()}]"
    choix = [debut + _mot_sans_casse(mot[1:])]
    for pli, signes in _PLIAGES.items():
        if len(pli) > 1 and mot.lower().startswith(pli):
            choix += [signe + _mot_sans_casse(mot[len(pli):]) for signe in signes]
    return choix[0] if len(choix) == 1 else f"({'|'.join(choix)})"


def _sans_casse(motif: str) -> str:
    """« \\.app$ » → « \\.[aA][pP][pP]$ » : APFS ne distingue pas Evil.APP d'Evil.app, ni hooKs (kelvin) de hooks,
    la cage non plus (on ne compte pas sur la façon dont macOS compare ses regex)."""
    sortie, mot, echappe, classe = [], "", False, False
    for c in motif:
        if c.isascii() and c.isalpha() and not echappe and not classe:
            mot += c
            continue
        sortie.append(_mot_sans_casse(mot))
        mot = ""
        sortie.append(c)
        if echappe:
            echappe = False
        elif c == "\\":
            echappe = True
        elif c == "[":
            classe = True
        elif c == "]":
            classe = False
    sortie.append(_mot_sans_casse(mot))
    return "".join(sortie)


def _plie(chemin: str) -> str:
    """Le chemin comme APFS le compare : sans casse ni ligatures (ﬁ → fi, K → k, ſ → s)."""
    return chemin.casefold()


def _cage_profil(permis: Path | None = None) -> str:
    maison = Path.home().resolve()
    dossiers, fichiers = _se_lance_chemins()
    motifs = " ".join(f'(regex #"{_sans_casse(m)}")' for m in _SE_LANCE_MOTIFS)
    # Leurs dossiers parents ne se renomment pas (sinon : renommer ~/Jervis et en reposer un faux à la place) ;
    # y créer un fichier reste permis, c'est le chemin de l'enfant qui compte.
    parents = [maison, maison / "Library", maison / "Library/Application Support", _RACINE]
    sp = lambda chemins, quoi: " ".join(f'({quoi} "{_sbpl(c)}")' for c in chemins)  # noqa: E731
    # Le refus de process-exec ne vise que ces chemins (une copie du binaire y échappe, et Python appelle les mêmes
    # services sans binaire) : ce qui ferme vraiment la porte, ce sont les services eux-mêmes — Apple Events,
    # l'écran, LaunchServices (lancer une app, ouvrir un fichier), les éléments de connexion, Raccourcis et les
    # actions d'apps (App Intents), et job-creation : aucune tâche confiée à launchd, qui tournerait hors cage.
    return ("(version 1)(allow default)"
            "(deny appleevent-send)(deny job-creation)"
            '(deny mach-lookup (global-name "com.apple.coreservices.appleevents")'
            ' (global-name "com.apple.coreservices.launchservicesd") (global-name-prefix "com.apple.lsd.")'
            ' (global-name-prefix "com.apple.windowserver") (global-name-prefix "com.apple.backgroundtaskmanagement")'
            ' (global-name-prefix "com.apple.shortcuts") (global-name-prefix "com.apple.WorkflowKit")'
            ' (global-name-prefix "com.apple.siriactionsd") (global-name-prefix "com.apple.linkd"))'
            f"(deny file-write* {sp(dossiers, 'subpath')} {sp(fichiers, 'literal')} {motifs})"
            f"(deny file-write-unlink {sp(parents, 'literal')})"
            f"(deny process-exec {sp(_CAGE_INTERDITS, 'literal')})"
            + (f'(allow file-write* (subpath "{_sbpl(permis)}"))' if permis else ""))  # en dernier : il l'emporte


def encager(commande: str, permis: Path | None = None) -> str:
    """La même commande, dans la cage (même shell, même dossier, même sortie) ; permis : le seul dossier qu'elle peut
    écrire en entier, .git compris (le nouveau dépôt d'un git clone ou git init que tu as accepté)."""
    profil = shlex.quote(_cage_profil(permis))
    return f"MPLBACKEND=Agg GIT_OPTIONAL_LOCKS=0 {CAGE_EXEC} -p {profil} /bin/zsh -c {shlex.quote(commande)}"


def a_encager(commande: str) -> bool:
    """Ce qui n'a pas besoin de la cage : la courte liste anodine (elle pilote Spotify, ouvre une page), un « cd ».
    Une commande qui lance elle-même sandbox-exec est encagée comme les autres : une cage dans la cage n'ajoute que
    des interdits, alors qu'un profil « (allow default) » à lui seul en sortirait."""
    commande = commande.strip()
    return not (commande_anodine(commande) or re.fullmatch(r"cd(?:\s+[^\s;&|`$<>()]+)?", commande))


# Après un texte venu d'ailleurs, écrire par Write ou Edit dans ce qui se lance tout seul, c'est toi qui dis oui
# (les mêmes chemins que la cage, plus les LaunchAgents/LaunchDaemons du système).
SE_LANCE_PLUS_TARD = re.compile("|".join(
    [r"Library/(?:LaunchAgents|LaunchDaemons|Messages)\b", r"com\.apple\.backgroundtaskmanagement",
     # où que soit le dossier (un autre compte, un autre dépôt, un chemin pas encore résolu)
     r"/\.claude/", r"/\.ssh/", r"/\.config/(?:git|fish)/", r"/\.git/(?:hooks|info)/",
     r"/\.git/(?:config|config\.worktree|commondir)$"]
    + [re.escape(str(d)) + r"(?:/|$)" for d in _se_lance_chemins()[0]]
    + [re.escape(str(f)) + "$" for f in _se_lance_chemins()[1]] + list(_SE_LANCE_MOTIFS)), re.IGNORECASE)


def ecrit_ce_qui_se_lance(outil: str, entree: dict[str, Any]) -> bool:
    if outil not in _ECRIT_DU_CODE or outil == "Bash":
        return False
    chemin = str(entree.get("file_path") or entree.get("notebook_path") or "")
    try:
        chemin = str(Path(chemin).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        pass
    return bool(SE_LANCE_PLUS_TARD.search(chemin) or SE_LANCE_PLUS_TARD.search(_plie(chemin)))


# Ce que ses outils ont reçu ces 15 dernières minutes (tous les agents) : un iMessage « de toi » qui reprend l'un de
# ces textes vient de lui, pas de toi (distance.vient_de_lui).
_gestes: deque[tuple[str, float]] = deque(maxlen=300)
GESTES_S = 900


def _reduit(texte: str) -> str:
    return " ".join(texte.split()).casefold()


def noter_outil(entree: Any) -> None:
    if texte := _reduit(_texte_de(entree)):
        _gestes.append((texte[:20000], time.monotonic()))


def vu_dans_ses_outils(texte: str) -> bool:
    """Vrai si ce texte (assez long pour ne pas être un « oui ») est passé par l'un de ses outils récemment."""
    t, maintenant = _reduit(texte), time.monotonic()
    return len(t) >= 8 and any(t in g for g, quand in _gestes if maintenant - quand < GESTES_S)


# Une question ❓ par iMessage attend ta réponse (distance.Boite.demander) : d'ici là, aucun de ses agents ne lance
# ce qui pourrait écrire dans Messages (commande, fichier, geste à l'écran). Sans quoi un geste lancé en même temps
# pourrait répondre « oui » à ta place dans ta conversation avec toi-même.
_questions_en_cours = 0


class QuestionEnCours:
    def __enter__(self) -> None:
        global _questions_en_cours
        _questions_en_cours += 1

    def __exit__(self, *_exc) -> None:
        global _questions_en_cours
        _questions_en_cours -= 1


def peut_ecrire_dans_messages(outil: str) -> bool:
    return outil in _ECRIT_DU_CODE or outil.startswith("mcp__mac__") or outil == "mcp__jervis__envoyer_imessage"


async def attendre_les_questions() -> None:
    while _questions_en_cours:
        await asyncio.sleep(0.2)


# Les gestes qui peuvent écrire quelque part : ceux de GESTES_MAC, plus les raccourcis (⌘V, Entrée) et le glisser.
GESTES_VERS_MESSAGES = GESTES_MAC | {"hotkey", "key", "press_key", "drag", "swipe"}


def _vise_messages(action: str, entree: dict[str, Any], apps_vues: Iterable[str]) -> bool:
    """Un geste (taper, cliquer, coller, ⌘V…) qui arriverait dans la fenêtre de Messages. Sans app ni pid nommés,
    il va aussi à l'app du premier plan : celle-là compte toujours, même si see en a regardé une autre."""
    if action not in GESTES_VERS_MESSAGES:
        return False
    apps = cibles_mac(action, entree, apps_vues)
    if not entree.get("pid") and not entree.get("app"):
        apps = apps | {_app_devant() or INCONNUE}
    return any(re.fullmatch(r"messages", a.strip(), re.IGNORECASE) for a in apps)


class Gardien:
    """Branche la politique sur le SDK (c'est le `can_use_tool`).

    Quand Claude lance plusieurs fois la même action d'un coup (6 mails à la corbeille),
    il ne pose la question qu'une fois : ta réponse vaut pour toute la demande en cours.
    `nouvelle_demande()` efface ces réponses à chaque nouvelle phrase.

    Il retient aussi les apps que Steve a regardées (outil see de Peekaboo) : un clic ou une frappe
    arrive dans l'une d'elles, ce qui dit s'il faut te demander."""

    def __init__(self, confirmer: Callable[[str], Awaitable[bool]]) -> None:
        self._confirmer = confirmer
        # Il se méfie quand la demande ne vient pas de toi mais du compte rendu d'un agent (sa ronde, une tâche de
        # fond) : ce texte peut répéter un mail piégé. Alors rien ne se fait sans ton oui, sauf lire.
        self.mefiance = False
        # Il a lu un texte venu d'ailleurs pendant cette demande : ce qu'il note dans son carnet passe par toi.
        self.contenu_externe = False
        # Pareil, mais pour toute la conversation : ce qu'une page a dicté reste dans sa tête d'une demande à
        # l'autre, jusqu'à ce qu'elle reparte à neuf (conversation_neuve, après une longue pause).
        self.dehors_dans_le_fil = False
        # La demande vient de ton iPhone, par iMessage (distance.py) : tu n'es pas devant le Mac.
        self.a_distance = False
        self._verrou = asyncio.Lock()  # une question à la fois, même si les appels arrivent ensemble
        self._reponses: dict[str, bool] = {}
        self._apps_vues: set[str] = set()
        self._vu_cette_demande = False

    def nouvelle_demande(self, mefiance: bool = False) -> None:
        self._reponses.clear()
        self.mefiance, self.contenu_externe = mefiance, False
        # Les apps vues juste avant restent valables (« clique sur OK » juste après), jusqu'au prochain see.
        self._vu_cette_demande = False

    def a_lu_dehors(self) -> None:
        """Il vient de lire (ou va lire) un texte venu d'ailleurs : une page, un mail, une sortie de commande."""
        self.contenu_externe = self.dehors_dans_le_fil = True
        noter_lecture_dehors()

    def conversation_neuve(self) -> None:
        """La conversation de Claude repart de zéro (/clear, ou un Claude Code neuf) : plus rien d'ailleurs dedans."""
        self.dehors_dans_le_fil = False

    def _noter_vue(self, entree: dict[str, Any]) -> None:
        if not self._vu_cette_demande:
            self._apps_vues = set()
            self._vu_cette_demande = True
        self._apps_vues.add(nom_app(entree.get("app_target")))

    async def __call__(self, outil: str, entree: dict[str, Any], _ctx: ToolPermissionContext):
        if comptes.secret_au_presse_papiers():
            if lit_le_presse_papiers(outil, entree):
                return PermissionResultDeny(message="Un mot de passe est dans le presse-papiers : tant qu'il y est, tu "
                                                    "ne peux que cliquer, taper et coller (cmd+v). Colle-le dans le champ "
                                                    "mot de passe (et sa confirmation) : le reste se rouvre peu après.")
            if colle(outil, entree):
                comptes.apres_collage()
        if motif := motif_paiement(outil, entree):
            return PermissionResultDeny(message=motif)
        noter_outil(entree)
        if peut_ecrire_dans_messages(outil):
            await attendre_les_questions()  # une question ❓ attend sa réponse par iMessage : on attend avec
        if envoie_par_messages(outil, entree) or (
                outil.startswith("mcp__mac__") and _vise_messages(nom_action(outil), entree, self._apps_vues)):
            return PermissionResultDeny(message="Pas de Messages par un script ni par des gestes à l'écran : pour un "
                                                "iMessage, utilise l'outil envoyer_imessage (ce qu'il envoie est noté, "
                                                "et jamais relu comme un ordre de lui).")
        if outil == "mcp__mac__see":
            self._noter_vue(entree)
        decision = politique(outil, entree, self._apps_vues)
        # Avant cet outil-ci (une commande lit aussi le dehors), dans cette demande ou plus tôt dans la conversation.
        deja_dehors = self.contenu_externe or self.mefiance or self.dehors_dans_le_fil
        # Le dossier d'un git clone / init, trouvé une seule fois : c'est lui qu'on te montre et lui seul qui s'ouvre
        depot = nouveau_depot(str(entree.get("command") or "")) if outil == "Bash" and deja_dehors else None
        if lit_dehors(outil):
            self.a_lu_dehors()
        if outil == "mcp__jervis__noter_mot" and (self.contenu_externe or self.mefiance):
            # Son lexique réécrit ce que tu dis avant que Claude le lise : il ne s'apprend que de ta bouche, jamais
            # d'un mail, d'une page ou d'un agent, et quel que soit le mode (même autonome, pas de question : non).
            return PermissionResultDeny(message="Après un texte venu d'ailleurs, je n'écris pas dans mon lexique : il ne "
                                                "s'apprend que de ce qu'il dit. La relecture de fin de conversation "
                                                "relèvera sa correction.")
        if outil == "mcp__jervis__appeler":
            if self.a_distance:  # l'appel partirait du Mac, où personne ne parle
                return PermissionResultDeny(message="Il t'écrit depuis son iPhone, loin du Mac : un appel lancé d'ici "
                                                    "sonnerait sur le Mac, sans lui. Dis-lui d'appeler depuis son iPhone "
                                                    "(ou « Dis Siri, appelle … »).")
            if decision is Decision.OUI and (self.contenu_externe or self.mefiance
                                             or appels.numero_urgence(appels.numero_appelable(entree.get("numero")))):
                # Un numéro lu sur une page ou dans un mail, ou un numéro d'urgence : c'est toi qui dis oui, même
                # en mode autonome (un appel ne se rattrape pas).
                decision = Decision.DEMANDER
        if decision is Decision.OUI and deja_dehors and (
                pilote_des_apps(outil, entree) or ecrit_ce_qui_se_lance(outil, entree)
                or depot):
            # Des frappes, un script d'app, un « open » de fichier, ou un fichier qui se lancera tout seul plus tard
            # (LaunchAgents, ton shell, le code de Steve) après une page lue : même en autonome.
            decision = Decision.DEMANDER
        prudent = config.CONFIRMATIONS != "autonome"   # en mode autonome : plus de questions que pour l'argent
        if prudent and decision is Decision.OUI and self.mefiance and not est_lecture(outil):
            decision = Decision.DEMANDER  # le compte rendu d'un agent n'est pas toi : il ne fait rien de lui-même
        if (prudent and decision is Decision.OUI and (self.mefiance or self.contenu_externe)
                and outil.startswith("mcp__jervis__") and outil.split("__")[-1] in ECRIT_POUR_PLUS_TARD):
            decision = Decision.DEMANDER  # ce qui viendrait d'un mail ou d'une page ne s'inscrit pas sans toi
        if decision is Decision.NON and (motif := motif_interdit(outil, entree)):
            return PermissionResultDeny(message=motif)  # refusé d'office : pas de question, et il sait quoi faire à la place
        if decision is Decision.OUI and deja_dehors and outil == "Bash" and commande_anodine(str(entree.get("command") or "")):
            # la courte liste, hors cage ; une app, par son chemin dans /Applications (pas une homonyme posée ailleurs)
            return PermissionResultAllow(updated_input={**entree, "command": avec_chemin_app(str(entree["command"]))})
        if decision is Decision.OUI and deja_dehors and outil == "Bash" and a_encager(str(entree.get("command") or "")):
            if Path(CAGE_EXEC).exists():  # seul, mais dans la cage (après ton oui à une question, sans)
                return PermissionResultAllow(updated_input={**entree, "command": encager(str(entree["command"]))})
            decision = Decision.DEMANDER  # pas de cage sur ce Mac : c'est toi qui dis oui
        if decision is Decision.DEMANDER:
            # Une réponse vaut pour toutes les actions identiques de la demande (6 mails à la corbeille),
            # mais chaque message envoyé à quelqu'un est confirmé à part.
            action = nom_action(outil)
            if outil.startswith("mcp__jervis__") and action in A_MONTRER:
                entree, trop_long = tel_que_montre(action, entree)  # c'est ce texte-là qui s'exécutera
                if trop_long:
                    return PermissionResultDeny(
                        message=f"« {trop_long} » est trop long pour que je le lui montre en entier avant de dire oui : "
                                f"redis-le en {A_MONTRER[action][trop_long]} caractères au plus, puis réessaie.")
            apps = cibles_mac(action, entree, self._apps_vues) if outil.startswith("mcp__mac__") else set()
            question = decrire(outil, entree, apps)
            if outil == "Bash" and deja_dehors:
                # Après une page lue, la description vient du modèle : c'est la commande elle-même qu'on te montre,
                # puisque c'est elle qui tournera hors cage
                commande = str(entree.get("command") or "").strip()
                if len(commande) > COMMANDE_MONTREE_MAX:
                    return PermissionResultDeny(
                        message=f"Cette commande est trop longue pour que je la lui montre en entier avant de dire oui : "
                                f"découpe-la en commandes de {COMMANDE_MONTREE_MAX} caractères au plus, puis réessaie.")
                # rien du modèle, que la commande ; ses « » deviennent des " pour qu'elle ne ferme pas la citation
                question = (f"Je vais lancer cette commande, qui n'écrira que dans {depot} : " if depot else
                            "Je vais lancer cette commande hors de la cage : ")
                question += f"« {commande.replace('«', '\"').replace('»', '\"')} ». Je peux ?"
            if outil.startswith("mcp__mac__") and (app := atelier(action, entree, apps)):
                cle = f"atelier:{app}"  # un seul « oui » pour tout le travail dans Cursor pendant cette demande
                question = _decrire_atelier(app, action, entree)
            elif outil in ECRITURE_FICHIERS:
                cle = f"fichiers:{Path(str(entree.get('file_path') or '')).parent}"
            elif outil.startswith("mcp__claude_ai") and action not in PAR_APPEL:
                cle = outil
            else:
                cle = f"{outil}:{sorted(entree.items(), key=str)}:{depot}"  # un autre dossier : une autre question
            async with self._verrou:
                if cle not in self._reponses:
                    self._reponses[cle] = await self._confirmer(question)
            decision = Decision.OUI if self._reponses[cle] else Decision.NON
        if decision is Decision.OUI:
            if depot and Path(CAGE_EXEC).exists():
                # ton oui à un git clone / init : dans la cage quand même, son seul dossier ouvert
                return PermissionResultAllow(updated_input={**entree, "command": encager(str(entree["command"]),
                                                                                         permis=depot)})
            return PermissionResultAllow(updated_input=entree)
        return PermissionResultDeny(
            message="L'utilisateur a refusé. N'insiste pas et ne tente pas d'autre moyen : "
                    "dis-lui simplement que tu n'as rien fait."
        )
