"""Ce que Jervis a le droit de faire seul, ce qu'il doit te demander, et ce qui est interdit.

Claude Code appelle le `Gardien` avant chaque outil qui n'est pas en lecture seule :
une commande shell, un outil Gmail ou Agenda, une page web, un clic dans une app…
"""

import asyncio
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from . import config


class Decision(Enum):
    OUI = "oui"            # il le fait sans demander
    DEMANDER = "demander"  # il te pose la question à voix haute et attend « oui »
    NON = "non"            # interdit, il te dira qu'il ne peut pas


def politique(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = (),
              mode: str | None = None) -> Decision:
    """Décide pour un appel d'outil, selon config.CONFIRMATIONS (« confiance » par défaut, « essentielles »
    ou « toutes »)."""
    mode = mode or config.CONFIRMATIONS
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
    r"|bash_profile|bash_login|profile|gitconfig)\b|\.config/(fish|git)\b|\.git/hooks|\.pth\b|sitecustomize|usercustomize"
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


def politique_confiance(outil: str, entree: dict[str, Any], apps_vues: Iterable[str] = ()) -> Decision:
    action = outil.split("__")[-1]
    if outil.startswith("mcp__jervis__"):  # iMessage : envoyé direct ; demander_accord : c'est lui qui veut demander
        return _maison(action, entree, {"demander_accord"})
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
        if INTERDIT.search(commande):
            return Decision.NON
        if BASH_LOURD.search(commande) or FICHIERS_SECRETS.search(commande) or PERSISTANCE.search(commande):
            return Decision.DEMANDER
        return Decision.OUI
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
    action = outil.split("__")[-1]
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
        if INTERDIT.search(commande):
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
        action = outil.split("__")[-1]
        return Decision.OUI if action.startswith(LECTURE) else Decision.DEMANDER
    if outil in {"Task", "Agent"}:  # il confie une tâche à son expert : chaque outil de l'expert repasse par ici
        return Decision.OUI
    if outil in LECTURE_FICHIERS:  # lire et chercher dans tes fichiers : seul, sauf les secrets
        return Decision.DEMANDER if FICHIERS_SECRETS.search(chemin_fichier(entree)) else Decision.OUI
    if outil == "Bash":
        commande = entree.get("command", "").strip()
        if INTERDIT.search(commande):
            return Decision.NON
        if SANS_RISQUE.match(commande) and not re.search(r"[;&|`$<>]", commande):
            return Decision.OUI
    return Decision.DEMANDER


# Outils maison confirmés hors du mode « confiance » : ceux qui parlent à quelqu'un d'autre que toi, et ceux
# qui effacent (la corbeille se rattrape, donc en mode « confiance » ils passent sans question).
JERVIS_A_CONFIRMER = {"envoyer_imessage", "demander_accord", "mettre_a_la_corbeille", "effacer_memoire"}


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
# Actions des connecteurs qui ne font que lire (Gmail, Agenda, Drive, et le catalogue de Zapier).
LECTURE = ("list", "get", "search", "suggest", "read", "download",
           "inspect_zapier", "discover_zapier", "execute_zapier_read")
# Commandes Bash que Jervis lance sans demander (une seule commande, sans enchaînement).
SANS_RISQUE = re.compile(
    r"""^(open\s+(-a\s+|"https?://|'https?://|https?://)"""
    r"""|osascript\s+-e\s+['"](set\s+volume|output\s+volume|tell\s+application\s+"(Spotify|Music)"\s+to\s+(play|pause|playpause|next\s+track|previous\s+track))"""
    r"""|date\b|pmset\s+-g|ls\b|cal\b)""",
    re.IGNORECASE,
)
INTERDIT = re.compile(r"\b(sudo|rm\s+-rf|mkfs|diskutil\s+erase|shutdown|reboot)\b")
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
    action = outil.split("__")[-1]
    if action == "demander_accord":
        return f"{entree.get('action', 'Je continue').strip().rstrip('.')}. Je peux ?"
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
    # Zapier
    "enable_zapier_action": "ajouter une action à ton Zapier",
    "disable_zapier_action": "retirer une action de ton Zapier",
}
# Ce qui part chez quelqu'un d'autre : confirmé à chaque fois, même s'il y en a plusieurs d'affilée.
PAR_APPEL = {"send_message", "reply", "forward", "share_file", "execute_zapier_write_action", "request_access"}


class Gardien:
    """Branche la politique sur le SDK (c'est le `can_use_tool`).

    Quand Claude lance plusieurs fois la même action d'un coup (6 mails à la corbeille),
    il ne pose la question qu'une fois : ta réponse vaut pour toute la demande en cours.
    `nouvelle_demande()` efface ces réponses à chaque nouvelle phrase.

    Il retient aussi les apps que Steve a regardées (outil see de Peekaboo) : un clic ou une frappe
    arrive dans l'une d'elles, ce qui dit s'il faut te demander."""

    def __init__(self, confirmer: Callable[[str], Awaitable[bool]]) -> None:
        self._confirmer = confirmer
        self._verrou = asyncio.Lock()  # une question à la fois, même si les appels arrivent ensemble
        self._reponses: dict[str, bool] = {}
        self._apps_vues: set[str] = set()
        self._vu_cette_demande = False

    def nouvelle_demande(self) -> None:
        self._reponses.clear()
        # Les apps vues juste avant restent valables (« clique sur OK » juste après), jusqu'au prochain see.
        self._vu_cette_demande = False

    def _noter_vue(self, entree: dict[str, Any]) -> None:
        if not self._vu_cette_demande:
            self._apps_vues = set()
            self._vu_cette_demande = True
        self._apps_vues.add(nom_app(entree.get("app_target")))

    async def __call__(self, outil: str, entree: dict[str, Any], _ctx: ToolPermissionContext):
        if outil == "mcp__mac__see":
            self._noter_vue(entree)
        decision = politique(outil, entree, self._apps_vues)
        if decision is Decision.DEMANDER:
            # Une réponse vaut pour toutes les actions identiques de la demande (6 mails à la corbeille),
            # mais chaque message envoyé à quelqu'un est confirmé à part.
            action = outil.split("__")[-1]
            apps = cibles_mac(action, entree, self._apps_vues) if outil.startswith("mcp__mac__") else set()
            question = decrire(outil, entree, apps)
            if outil.startswith("mcp__mac__") and (app := atelier(action, entree, apps)):
                cle = f"atelier:{app}"  # un seul « oui » pour tout le travail dans Cursor pendant cette demande
                question = _decrire_atelier(app, action, entree)
            elif outil in ECRITURE_FICHIERS:
                cle = f"fichiers:{Path(str(entree.get('file_path') or '')).parent}"
            elif outil.startswith("mcp__claude_ai") and action not in PAR_APPEL:
                cle = outil
            else:
                cle = f"{outil}:{sorted(entree.items(), key=str)}"
            async with self._verrou:
                if cle not in self._reponses:
                    self._reponses[cle] = await self._confirmer(question)
            decision = Decision.OUI if self._reponses[cle] else Decision.NON
        if decision is Decision.OUI:
            return PermissionResultAllow(updated_input=entree)
        return PermissionResultDeny(
            message="L'utilisateur a refusé. N'insiste pas et ne tente pas d'autre moyen : "
                    "dis-lui simplement que tu n'as rien fait."
        )
