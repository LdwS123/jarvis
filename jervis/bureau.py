"""Le bureau : les fenêtres, tes modes, la musique, le texte sélectionné, les dossiers en désordre, la santé du Mac
et la recherche dans tes fichiers. Tout se passe sur le Mac (AppleScript, Spotlight, les commandes de macOS) :
rien n'est envoyé ailleurs.

Ce qui touche à tes fichiers se rattrape : un rangement n'écrase jamais rien et se défait (annuler_rangement),
et il te le montre avant de le faire (ranger_dossier sans `appliquer` = juste le plan).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from . import config


def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _erreur(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "is_error": True}


async def _lancer(*cmd: str, entree: str | None = None, delai: float = 30) -> tuple[int, str]:
    from . import outils
    return await outils._lancer(*cmd, entree=entree, delai=delai)


async def _applescript(script: str, *args: str) -> tuple[int, str]:
    from . import outils
    return await outils._applescript(script, *args)


def _liste(valeur: Any) -> list[str]:
    """« Cursor, Slack » ou ["Cursor", "Slack"] → ["Cursor", "Slack"]."""
    if isinstance(valeur, str):
        valeur = re.split(r"[,;\n]", valeur)
    return [" ".join(str(v).split()) for v in (valeur or []) if str(v).strip()]


def _lire_json(fichier: Path, defaut: Any) -> Any:
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaut


def _ecrire_json(fichier: Path, valeur: Any) -> None:
    fichier.parent.mkdir(parents=True, exist_ok=True)
    tmp = fichier.with_suffix(".tmp")
    tmp.write_text(json.dumps(valeur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fichier)


# --- Les fenêtres ---------------------------------------------------------------------------------------------

DISPOSITIONS = ("plein", "gauche", "droite", "haut", "bas", "centre", "cote_a_cote", "grille", "principal")


def rectangles(disposition: str, ecran: tuple[int, int, int, int], n: int) -> list[tuple[int, int, int, int]]:
    """Où va chaque fenêtre (x, y, largeur, hauteur), sur la partie utile de l'écran (sans la barre des menus ni
    le Dock). `n` fenêtres : côte à côte = des colonnes, grille = 2×2 (ou 2×3…), principal = une grande à gauche
    et les autres empilées à droite."""
    x, y, l, h = ecran
    n = max(1, n)
    if disposition == "gauche":
        return [(x, y, l // 2, h)]
    if disposition == "droite":
        return [(x + l - l // 2, y, l // 2, h)]
    if disposition == "haut":
        return [(x, y, l, h // 2)]
    if disposition == "bas":
        return [(x, y + h - h // 2, l, h // 2)]
    if disposition == "centre":
        lc, hc = l * 2 // 3, h * 4 // 5
        return [(x + (l - lc) // 2, y + (h - hc) // 2, lc, hc)]
    if disposition == "cote_a_cote" and n > 1:
        largeur = l // n
        return [(x + i * largeur, y, largeur if i < n - 1 else l - largeur * (n - 1), h) for i in range(n)]
    if disposition == "grille" and n > 1:
        colonnes = 2 if n <= 4 else 3
        rangees = -(-n // colonnes)
        lc, hc = l // colonnes, h // rangees
        return [(x + (i % colonnes) * lc, y + (i // colonnes) * hc, lc, hc) for i in range(n)]
    if disposition == "principal" and n > 1:
        grande = l * 3 // 5
        hc = h // (n - 1)
        return [(x, y, grande, h)] + [(x + grande, y + i * hc, l - grande, hc) for i in range(n - 1)]
    return [(x, y, l, h)] * n   # plein écran (sans le mode plein écran de macOS, qui cache tout le reste)


_ECRAN = """ObjC.import('AppKit');
var s = $.NSScreen.mainScreen, f = s.frame, v = s.visibleFrame;
JSON.stringify([v.origin.x, f.size.height - v.origin.y - v.size.height, v.size.width, v.size.height]);"""

_PLACER = """on run argv
set nomApp to item 1 of argv
tell application "System Events"
    if not (exists application process nomApp) then return "absente"
    tell application process nomApp
        if (count of windows) = 0 then return "sans fenêtre"
        set frontmost to true
        set position of window 1 to {(item 2 of argv) as integer, (item 3 of argv) as integer}
        set size of window 1 to {(item 4 of argv) as integer, (item 5 of argv) as integer}
    end tell
end tell
return "ok"
end run"""

_DEVANT = """tell application "System Events" to get name of first application process whose frontmost is true"""


@tool("ranger_fenetres", "Range les fenêtres à l'écran (« mets Cursor à gauche et Chrome à droite », « agrandis », "
      "« mets mes trois apps en grille »). `disposition` : plein, gauche, droite, haut, bas, centre, cote_a_cote, "
      "grille, principal (une grande à gauche, les autres à droite). `apps` : les apps dans l'ordre (sinon celle "
      "de devant). Une fenêtre par app (la première).",
      {"type": "object", "properties": {"disposition": {"type": "string", "enum": list(DISPOSITIONS)},
                                        "apps": {"type": "array", "items": {"type": "string"}}},
       "required": ["disposition"]})
async def ranger_fenetres(args: dict[str, Any]) -> dict[str, Any]:
    disposition = str(args.get("disposition") or "plein")
    apps = _liste(args.get("apps"))
    if not apps:
        code, devant = await _applescript(_DEVANT)
        if code or not devant:
            return _erreur(f"Je ne vois pas l'app de devant ({devant}). Il faut l'autorisation Accessibilité.")
        apps = [devant]
    code, sortie = await _lancer("osascript", "-l", "JavaScript", "-e", _ECRAN)
    try:
        ecran = tuple(int(v) for v in json.loads(sortie))
    except (ValueError, TypeError):
        return _erreur(f"Je ne trouve pas la taille de l'écran ({sortie}).")
    places, rates = [], []
    for app, (x, y, l, h) in zip(apps, rectangles(disposition, ecran, len(apps))):  # type: ignore[arg-type]
        code, sortie = await _applescript(_PLACER, app, str(x), str(y), str(l), str(h))
        (places if code == 0 and sortie == "ok" else rates).append(f"{app} ({sortie})" if sortie != "ok" else app)
    if rates and not places:
        return _erreur("Pas pu ranger : " + ", ".join(rates) + ". (Accessibilité autorisée pour Steve ?)")
    return _texte(f"Rangé ({disposition}) : {', '.join(places)}" + (f". Pas pu : {', '.join(rates)}" if rates else "."))


# --- Les modes ------------------------------------------------------------------------------------------------

def modes() -> dict[str, dict[str, Any]]:
    return _lire_json(config.FICHIER_MODES, {})


def _cle(nom: str) -> str:
    return " ".join(str(nom).casefold().split()).removeprefix("mode ").strip()


@tool("definir_mode", "Crée ou change un de ses modes (« crée un mode travail : ouvre Cursor et Slack, ferme "
      "Messages, volume à 20 »). `ouvrir` / `fermer` : des apps séparées par des virgules ; `liens` : des adresses ; "
      "`volume` : 0-100 ; `raccourci` : un raccourci de l'app Raccourcis à lancer (pour un mode Concentration de "
      "macOS) ; `musique` : ce qu'il met (ou « pause ») ; `fenetres` : une disposition de ranger_fenetres pour les "
      "apps ouvertes. `supprimer` : vrai pour l'effacer.",
      {"type": "object", "properties": {"nom": {"type": "string"}, "ouvrir": {"type": "string"},
                                        "fermer": {"type": "string"}, "liens": {"type": "string"},
                                        "volume": {"type": "integer"}, "raccourci": {"type": "string"},
                                        "musique": {"type": "string"}, "fenetres": {"type": "string"},
                                        "supprimer": {"type": "boolean"}}, "required": ["nom"]})
async def definir_mode(args: dict[str, Any]) -> dict[str, Any]:
    nom = _cle(args.get("nom") or "")
    if not nom:
        return _erreur("Il faut un nom de mode.")
    tous = modes()
    if args.get("supprimer"):
        if tous.pop(nom, None) is None:
            return _erreur(f"Pas de mode « {nom} ».")
        _ecrire_json(config.FICHIER_MODES, tous)
        return _texte(f"Mode « {nom} » effacé.")
    mode = {"ouvrir": _liste(args.get("ouvrir")), "fermer": _liste(args.get("fermer")),
            "liens": [l for l in _liste(args.get("liens")) if re.match(r"https?://", l)],
            "volume": args.get("volume") if isinstance(args.get("volume"), int) else None,
            "raccourci": str(args.get("raccourci") or "").strip(), "musique": str(args.get("musique") or "").strip(),
            "fenetres": str(args.get("fenetres") or "").strip() if args.get("fenetres") in DISPOSITIONS else ""}
    tous[nom] = {k: v for k, v in mode.items() if v not in ("", [], None)}
    _ecrire_json(config.FICHIER_MODES, tous)
    return _texte(f"Mode « {nom} » enregistré : {decrire_mode(tous[nom])}.")


def decrire_mode(mode: dict[str, Any]) -> str:
    morceaux = []
    if mode.get("ouvrir"):
        morceaux.append("ouvre " + ", ".join(mode["ouvrir"]))
    if mode.get("fermer"):
        morceaux.append("ferme " + ", ".join(mode["fermer"]))
    if mode.get("liens"):
        morceaux.append(f"{len(mode['liens'])} lien(s)")
    if mode.get("volume") is not None:
        morceaux.append(f"volume {mode['volume']} %")
    if mode.get("raccourci"):
        morceaux.append(f"raccourci « {mode['raccourci']} »")
    if mode.get("musique"):
        morceaux.append(f"musique : {mode['musique']}")
    if mode.get("fenetres"):
        morceaux.append(f"fenêtres : {mode['fenetres']}")
    return ", ".join(morceaux) or "rien"


_QUITTER = """on run argv
set nomApp to item 1 of argv
if application nomApp is running then tell application nomApp to quit
end run"""


@tool("mode", "Passe dans un de tes modes (« mode travail », « mode détente », « mode focus ») : ouvre et ferme "
      "les apps, les liens, le volume, la musique, les fenêtres, et le raccourci de concentration. Sans `nom` : la "
      "liste de ses modes.", {"type": "object", "properties": {"nom": {"type": "string"}}})
async def mode(args: dict[str, Any]) -> dict[str, Any]:
    tous = modes()
    nom = _cle(args.get("nom") or "")
    if not nom:
        if not tous:
            return _texte("Aucun mode pour l'instant. Propose-lui d'en créer un avec definir_mode.")
        return _texte("Ses modes : " + " ; ".join(f"{n} ({decrire_mode(m)})" for n, m in tous.items()))
    m = tous.get(nom) or next((v for k, v in tous.items() if nom in k or k in nom), None)
    if m is None:
        return _erreur(f"Pas de mode « {nom} ». Ses modes : {', '.join(tous) or 'aucun'}. (definir_mode pour le créer)")
    faits, rates = [], []
    for app in m.get("fermer", []):
        code, _ = await _applescript(_QUITTER, app)
        (faits if code == 0 else rates).append(f"fermé {app}" if code == 0 else app)
    for app in m.get("ouvrir", []):
        code, _ = await _lancer("open", "-a", app)
        (faits if code == 0 else rates).append(f"ouvert {app}" if code == 0 else app)
    for lien in m.get("liens", []):
        if (await _lancer("open", lien))[0] == 0:
            faits.append("lien ouvert")
    if m.get("volume") is not None:
        volume = max(0, min(100, int(m["volume"])))
        if (await _applescript(f"set volume output volume {volume}"))[0] == 0:
            faits.append(f"volume {volume} %")
    if m.get("raccourci"):
        code, sortie = await _lancer("shortcuts", "run", m["raccourci"], delai=60)
        (faits if code == 0 else rates).append(f"raccourci {m['raccourci']}" if code == 0 else
                                               f"raccourci {m['raccourci']} ({sortie[:60]})")
    if m.get("musique"):
        action = "pause" if m["musique"].casefold() in {"pause", "stop", "rien"} else "chercher"
        resultat = await _musique(action, m["musique"])
        faits.append(resultat)
    if m.get("fenetres") and m.get("ouvrir"):
        await asyncio.sleep(1.5)  # le temps que les apps ouvrent leur fenêtre
        resultat = await ranger_fenetres.handler({"disposition": m["fenetres"], "apps": m["ouvrir"]})
        faits.append(resultat["content"][0]["text"])
    texte = f"Mode « {nom} » : " + ", ".join(faits or ["rien à faire"])
    return _texte(texte + (f". Pas pu : {', '.join(rates)}." if rates else "."))


# --- La musique -----------------------------------------------------------------------------------------------

_LECTEUR = """on run argv
set action to item 1 of argv
set appli to item 2 of argv
set valeur to item 3 of argv
if appli is "Spotify" then
    tell application "Spotify"
        if action is "lecture" then play
        if action is "pause" then pause
        if action is "suivant" then next track
        if action is "precedent" then previous track
        if action is "volume" then set sound volume to (valeur as integer)
        if action is "chercher" then
            open location ("spotify:search:" & valeur)
            delay 1.5
            play
        end if
        if player state is stopped then return "arrêté"
        return (player state as text) & " · " & (name of current track) & " — " & (artist of current track)
    end tell
else
    tell application "Music"
        if action is "lecture" then play
        if action is "pause" then pause
        if action is "suivant" then next track
        if action is "precedent" then previous track
        if action is "volume" then set sound volume to (valeur as integer)
        if action is "chercher" then
            set trouves to (search playlist "Library" for valeur)
            if trouves is {} then return "rien trouvé dans ta bibliothèque pour " & valeur
            play item 1 of trouves
        end if
        if player state is stopped then return "arrêté"
        return (player state as text) & " · " & (name of current track) & " — " & (artist of current track)
    end tell
end if
end run"""

_EN_MARCHE = """on run argv
if application "Spotify" is running then return "Spotify"
return "Music"
end run"""


async def _musique(action: str, valeur: str = "") -> str:
    _, appli = await _applescript(_EN_MARCHE)
    appli = "Spotify" if appli.strip() == "Spotify" else "Music"
    code, sortie = await _applescript(_LECTEUR, action, appli, valeur or "")
    if code:
        return f"{appli} ne répond pas ({sortie[:120]})"
    etat = sortie.replace("playing", "en lecture").replace("paused", "en pause")
    return f"{appli} : {etat}"


@tool("musique", "La musique (Spotify s'il est ouvert, sinon Musique) : `action` = lecture, pause, suivant, "
      "precedent, volume (`valeur` 0-100), quoi (ce qui joue), chercher (`valeur` = un titre, un artiste, un "
      "genre). Pour le volume du Mac entier, pas celui de la musique : osascript « set volume ».",
      {"type": "object", "properties": {"action": {"type": "string", "enum": ["lecture", "pause", "suivant",
                                                                              "precedent", "volume", "quoi",
                                                                              "chercher"]},
                                        "valeur": {"type": "string"}}, "required": ["action"]})
async def musique(args: dict[str, Any]) -> dict[str, Any]:
    action, valeur = str(args.get("action") or "quoi"), str(args.get("valeur") or "")
    if action == "volume" and not valeur.isdigit():
        return _erreur("Le volume : un nombre de 0 à 100.")
    if action == "chercher" and not valeur.strip():
        return _erreur("Que faut-il chercher ?")
    return _texte(await _musique(action, valeur))


# --- Le texte sélectionné -----------------------------------------------------------------------------------

_SELECTION = """tell application "System Events"
    set p to first application process whose frontmost is true
    set el to value of attribute "AXFocusedUIElement" of p
    return value of attribute "AXSelectedText" of el
end tell"""

_COPIER = """tell application "System Events" to keystroke "c" using command down"""

SELECTION_MAX = 20_000


@tool("texte_selectionne", "Le texte que tu as sélectionné en ce moment, dans n'importe quelle app (« résume ce "
      "que j'ai sélectionné », « traduis ça », « explique ce passage »). Il ne le modifie pas : pour le changer, "
      "propose de le copier (pbcopy) ou de l'écrire ailleurs.", {})
async def texte_selectionne(_args: dict[str, Any]) -> dict[str, Any]:
    code, texte = await _applescript(_SELECTION)
    if code or not texte.strip() or texte.strip() == "missing value":
        # Certaines apps (Chrome, Electron) ne donnent pas leur sélection : un Cmd+C, et le presse-papiers
        # d'avant est remis tout de suite.
        _, avant = await _lancer("pbpaste")
        await _applescript(_COPIER)
        await asyncio.sleep(0.25)
        _, texte = await _lancer("pbpaste")
        await _lancer("pbcopy", entree=avant)
        if texte == avant:
            texte = ""
    texte = texte.strip()
    if not texte:
        return _erreur("Rien de sélectionné (ou l'app ne le donne pas : copie-le, je lirai le presse-papiers).")
    if len(texte) > SELECTION_MAX:
        texte = texte[:SELECTION_MAX] + " (… coupé)"
    return _texte(f"Texte sélectionné ({len(texte)} caractères) :\n{texte}")


# --- Les dossiers en désordre -------------------------------------------------------------------------------

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".svg", ".tiff", ".bmp", ".raw"},
    "PDF": {".pdf"},
    "Documents": {".doc", ".docx", ".pages", ".odt", ".rtf", ".txt", ".md", ".epub"},
    "Tableurs": {".xls", ".xlsx", ".numbers", ".csv", ".ods"},
    "Présentations": {".ppt", ".pptx", ".key", ".odp"},
    "Vidéos": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"},
    "Musique": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"},
    "Installeurs": {".dmg", ".pkg", ".mpkg"},
    "Code": {".py", ".js", ".ts", ".json", ".html", ".css", ".sh", ".ipynb", ".swift", ".java", ".c", ".cpp",
             ".go", ".rs", ".rb", ".php", ".sql", ".yaml", ".yml", ".xml"},
}
EN_COURS = {".download", ".crdownload", ".part", ".partial", ".tmp", ".opdownload"}


def regles_rangement() -> list[dict[str, str]]:
    return [r for r in _lire_json(config.FICHIER_RANGEMENT, []) if isinstance(r, dict) and r.get("motif")
            and r.get("dossier")]


def dossier_permis(dossier: Path) -> str | None:
    """Pourquoi on ne range pas ce dossier-là (None s'il est permis) : seulement chez toi, jamais ta maison
    elle-même, ni la Bibliothèque, ni un dossier caché, ni un dossier d'app ou de code (.git)."""
    from .permissions import FICHIERS_SECRETS
    maison = Path.home().resolve()
    try:
        dossier = dossier.resolve()
        relatif = dossier.relative_to(maison)
    except (OSError, ValueError):
        return "il n'est pas dans ton dossier personnel"
    if dossier == maison:
        return "c'est ton dossier personnel lui-même"
    if relatif.parts[0] == "Library" or any(p.startswith(".") for p in relatif.parts):
        return "c'est un dossier système ou caché"
    if FICHIERS_SECRETS.search(str(dossier)) or (dossier / ".git").exists() or dossier.suffix == ".app":
        return "c'est un dossier de code, d'app ou de secrets"
    if not dossier.is_dir():
        return "ce n'est pas un dossier"
    return None


def plan_rangement(dossier: Path, regles: list[dict[str, str]] | None = None) -> list[tuple[Path, Path]]:
    """[(fichier, sous-dossier où il ira)] : tes règles d'abord (un mot du nom → un dossier), puis le genre du
    fichier. Les fichiers cachés, en cours de téléchargement ou sans genre connu restent où ils sont, et les
    dossiers aussi."""
    regles = regles_rangement() if regles is None else regles
    plan = []
    for f in sorted(dossier.iterdir(), key=lambda p: p.name.casefold()):
        if f.name.startswith(".") or not f.is_file() or f.is_symlink() or f.suffix.lower() in EN_COURS:
            continue
        nom = f.name.casefold()
        cible = next((r["dossier"] for r in regles if r["motif"].casefold() in nom), None)
        if cible is None:
            cible = next((c for c, ext in CATEGORIES.items() if f.suffix.lower() in ext), None)
        if cible is None:
            continue
        cible = cible.strip().rstrip("/")
        if cible.startswith("~") or os.path.isabs(cible):  # « ~/Documents/Factures » : là, pas sous le dossier
            destination = Path(os.path.expanduser(cible))
        elif Path(cible).name.casefold() == dossier.name.casefold():
            continue  # une photo déjà dans « Images » n'ira pas dans « Images/Images »
        else:
            destination = dossier / cible
        if destination.resolve() != f.parent.resolve():
            plan.append((f, destination))
    return plan


def _libre(chemin: Path) -> Path:
    """Le même nom, ou « nom (2).ext » s'il est pris : on n'écrase jamais rien."""
    n, libre = 2, chemin
    while libre.exists():
        libre = chemin.with_name(f"{chemin.stem} ({n}){chemin.suffix}")
        n += 1
    return libre


def appliquer_rangement(plan: list[tuple[Path, Path]], dossier: Path) -> tuple[int, Path | None]:
    """Déplace, et note de quoi tout remettre (annuler_rangement)."""
    faits: list[list[str]] = []
    for f, destination in plan:
        if dossier_permis(destination) not in (None, "ce n'est pas un dossier"):
            continue  # une règle qui enverrait hors de chez toi, dans la Bibliothèque… : ignorée
        destination.mkdir(parents=True, exist_ok=True)
        cible = _libre(destination / f.name)
        try:
            shutil.move(str(f), str(cible))
        except OSError:
            continue
        faits.append([str(cible), str(f)])
    if not faits:
        return 0, None
    config.DOSSIER_ANNULER_RANGEMENT.mkdir(parents=True, exist_ok=True)
    journal = config.DOSSIER_ANNULER_RANGEMENT / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
    _ecrire_json(journal, {"dossier": str(dossier), "deplaces": faits})
    return len(faits), journal


def resume_plan(plan: list[tuple[Path, Path]], dossier: Path) -> str:
    par_dossier: dict[str, list[str]] = {}
    for f, destination in plan:
        cle = str(destination.relative_to(dossier)) if destination.is_relative_to(dossier) else str(destination)
        par_dossier.setdefault(cle, []).append(f.name)
    lignes = []
    for d, noms in sorted(par_dossier.items(), key=lambda x: -len(x[1])):
        exemples = ", ".join(noms[:4]) + (f"… (+{len(noms) - 4})" if len(noms) > 4 else "")
        lignes.append(f"→ {d}/ : {len(noms)} fichier(s) ({exemples})")
    return "\n".join(lignes)


@tool("ranger_dossier", "Range un dossier en désordre (Téléchargements, Bureau…) : chaque fichier dans un "
      "sous-dossier selon tes règles (noter_regle_rangement) puis son genre (Images, PDF, Documents, Installeurs…). "
      "D'abord SANS `appliquer` : le plan, à lui résumer. Puis, s'il est d'accord, avec `appliquer` = vrai. Rien "
      "n'est écrasé ni effacé, et annuler_rangement remet tout.",
      {"type": "object", "properties": {"dossier": {"type": "string"}, "appliquer": {"type": "boolean"}},
       "required": ["dossier"]})
async def ranger_dossier(args: dict[str, Any]) -> dict[str, Any]:
    dossier = Path(os.path.expanduser(str(args.get("dossier") or "~/Downloads")))
    if not dossier.is_absolute():
        dossier = Path.home() / dossier
    if raison := dossier_permis(dossier):
        return _erreur(f"Je ne range pas {dossier} : {raison}.")
    plan = await asyncio.to_thread(plan_rangement, dossier)
    if not plan:
        return _texte(f"{dossier} est déjà rangé (rien à déplacer).")
    if not args.get("appliquer"):
        return _texte(f"Plan pour {dossier} ({len(plan)} fichiers) :\n{resume_plan(plan, dossier)}\n"
                      "Rien n'est encore déplacé : demande-lui s'il est d'accord, puis rappelle avec appliquer.")
    n, journal = await asyncio.to_thread(appliquer_rangement, plan, dossier)
    return _texte(f"Rangé : {n} fichier(s) déplacé(s) dans {dossier}. Pour tout remettre : annuler_rangement."
                  if journal else "Rien n'a pu être déplacé.")


@tool("noter_regle_rangement", "Retient une règle de rangement, pour toujours (« les factures vont dans "
      "Documents/Factures », « les captures d'écran dans Images/Captures »). `motif` : un mot du nom de fichier ; "
      "`dossier` : où il va (relatif au dossier rangé, ou un chemin ~/…). `supprimer` : vrai pour l'oublier.",
      {"type": "object", "properties": {"motif": {"type": "string"}, "dossier": {"type": "string"},
                                        "supprimer": {"type": "boolean"}}, "required": ["motif"]})
async def noter_regle_rangement(args: dict[str, Any]) -> dict[str, Any]:
    motif = " ".join(str(args.get("motif") or "").split())
    dossier = str(args.get("dossier") or "").strip()
    if not motif:
        return _erreur("Il faut un motif (un mot du nom de fichier).")
    regles = [r for r in regles_rangement() if r["motif"].casefold() != motif.casefold()]
    if not args.get("supprimer"):
        if not dossier or ".." in Path(dossier).parts:
            return _erreur("Il faut un dossier (sans « .. »).")
        regles.insert(0, {"motif": motif, "dossier": dossier})
    _ecrire_json(config.FICHIER_RANGEMENT, regles)
    return _texte(f"Règle oubliée : « {motif} »." if args.get("supprimer")
                  else f"Retenu : les fichiers avec « {motif} » vont dans {dossier}.")


@tool("annuler_rangement", "Défait le dernier rangement (ranger_dossier) : chaque fichier revient où il était.", {})
async def annuler_rangement(_args: dict[str, Any]) -> dict[str, Any]:
    journaux = sorted(config.DOSSIER_ANNULER_RANGEMENT.glob("*.json")) if config.DOSSIER_ANNULER_RANGEMENT.exists() \
        else []
    if not journaux:
        return _texte("Aucun rangement à défaire.")
    journal = journaux[-1]
    donnees = _lire_json(journal, {})
    remis, rates = 0, 0
    for cible, origine in reversed(donnees.get("deplaces", [])):
        cible, origine = Path(cible), Path(origine)
        if not cible.exists():
            rates += 1
            continue
        try:
            shutil.move(str(cible), str(_libre(origine)))
            remis += 1
        except OSError:
            rates += 1
    journal.rename(journal.with_suffix(".annule"))
    dossier = Path(donnees.get("dossier") or "/")
    vides = {p for c, _ in donnees.get("deplaces", []) for p in Path(c).parents
             if p.is_relative_to(dossier) and p != dossier}
    for d in sorted(vides, key=lambda p: len(p.parts), reverse=True):  # Documents/Factures avant Documents
        try:
            d.rmdir()  # les sous-dossiers créés pour l'occasion, s'ils sont redevenus vides
        except OSError:
            pass
    return _texte(f"Remis en place : {remis} fichier(s)" + (f" ({rates} introuvables, déplacés depuis)" if rates
                                                            else "") + ".")


# --- La santé du Mac ----------------------------------------------------------------------------------------

def _sortie(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def mesurer() -> dict[str, Any]:
    """Disque, batterie, mémoire, charge, les apps qui mangent le processeur, la chaleur. Les commandes de macOS,
    rien d'installé."""
    m: dict[str, Any] = {}
    try:
        disque = shutil.disk_usage("/")
        m["disque_libre_go"] = round(disque.free / 1e9, 1)
        m["disque_total_go"] = round(disque.total / 1e9, 1)
    except OSError:
        pass
    batt = _sortie("pmset", "-g", "batt")
    if p := re.search(r"(\d+)%;\s*([^;]+);", batt):
        m["batterie"] = int(p.group(1))
        m["en_charge"] = "discharging" not in p.group(2) and "décharge" not in p.group(2)
    if "AC Power" in batt:
        m["en_charge"] = True
    if p := re.search(r'"CycleCount" = (\d+)', _sortie("ioreg", "-rc", "AppleSmartBattery")):
        m["cycles_batterie"] = int(p.group(1))
    pression = _sortie("memory_pressure", "-Q")
    if p := re.search(r"free percentage:\s*(\d+)%", pression):
        m["memoire_libre_pct"] = int(p.group(1))
    try:
        m["charge"] = round(os.getloadavg()[0], 1)
        m["coeurs"] = os.cpu_count() or 1
    except OSError:
        pass
    apps = []
    for ligne in _sortie("ps", "-Aceo", "pcpu,rss,comm", "-r").splitlines()[1:6]:
        if p := re.match(r"\s*([\d.,]+)\s+(\d+)\s+(.+)", ligne):
            apps.append((p.group(3).strip(), float(p.group(1).replace(",", ".")), int(p.group(2)) // 1024))
    m["apps"] = apps
    therm = _sortie("pmset", "-g", "therm")
    if p := re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", therm):
        m["vitesse_processeur_pct"] = int(p.group(1))
    return m


def alertes(m: dict[str, Any], deja: dict[str, str], aujourdhui: str | None = None,
            avant: dict[str, Any] | None = None) -> list[str]:
    """Ce qui mérite de te prévenir, une fois par jour et par souci (`deja` : souci → jour où il l'a dit).
    `avant` : la mesure précédente (une app ne compte que si elle mange le processeur deux fois de suite)."""
    jour = aujourdhui or date.today().isoformat()
    soucis: dict[str, str] = {}
    if m.get("disque_libre_go") is not None and m["disque_libre_go"] < config.SANTE_DISQUE_MIN_GO:
        soucis["disque"] = (f"il ne reste que {m['disque_libre_go']:.0f} Go sur le disque. Je peux regarder ce qui "
                            "prend de la place, ou ranger tes Téléchargements.")
    if m.get("batterie") is not None and not m.get("en_charge") and m["batterie"] <= config.SANTE_BATTERIE_MIN:
        soucis["batterie"] = f"la batterie est à {m['batterie']} %, branche le chargeur."
    if m.get("memoire_libre_pct") is not None and m["memoire_libre_pct"] < 10:
        soucis["memoire"] = f"la mémoire est presque pleine ({m['memoire_libre_pct']} % de libre)."
    if m.get("vitesse_processeur_pct") is not None and m["vitesse_processeur_pct"] < 70:
        soucis["chaleur"] = (f"le Mac chauffe : le processeur est bridé à {m['vitesse_processeur_pct']} % de "
                             "sa vitesse.")
    avant_apps = {nom for nom, cpu, _ in (avant or {}).get("apps", []) if cpu >= config.SANTE_CPU_MAX}
    for nom, cpu, _ in m.get("apps", []):
        if cpu >= config.SANTE_CPU_MAX and nom in avant_apps and not nom.startswith(("kernel_task", "WindowServer")):
            soucis[f"cpu:{nom}"] = f"{nom} mange {cpu:.0f} % du processeur depuis un moment."
    nouveaux = []
    for cle, texte in soucis.items():
        if deja.get(cle) != jour:
            deja[cle] = jour
            nouveaux.append(texte)
    return nouveaux


def bilan(m: dict[str, Any]) -> str:
    morceaux = []
    if "disque_libre_go" in m:
        morceaux.append(f"disque : {m['disque_libre_go']:.0f} Go libres sur {m['disque_total_go']:.0f}")
    if "batterie" in m:
        morceaux.append(f"batterie : {m['batterie']} %" + (" (en charge)" if m.get("en_charge") else "")
                        + (f", {m['cycles_batterie']} cycles" if "cycles_batterie" in m else ""))
    if "memoire_libre_pct" in m:
        morceaux.append(f"mémoire : {m['memoire_libre_pct']} % libre")
    if "charge" in m:
        morceaux.append(f"charge : {m['charge']} pour {m['coeurs']} cœurs")
    if "vitesse_processeur_pct" in m:
        morceaux.append("température normale" if m["vitesse_processeur_pct"] >= 100
                        else f"processeur bridé à {m['vitesse_processeur_pct']} % (il chauffe)")
    if m.get("apps"):
        morceaux.append("ce qui travaille le plus : "
                        + ", ".join(f"{n} {c:.0f} % ({mo} Mo)" for n, c, mo in m["apps"][:4]))
    return " ; ".join(morceaux) or "Je n'arrive pas à mesurer le Mac."


@tool("sante_du_mac", "L'état du Mac : place sur le disque, batterie (et ses cycles), mémoire, charge, les apps "
      "qui mangent le processeur, la chaleur (« pourquoi mon Mac rame ? », « il me reste combien de place ? »). "
      "Il te prévient aussi de lui-même quand ça coince.", {})
async def sante_du_mac(_args: dict[str, Any]) -> dict[str, Any]:
    return _texte(bilan(await asyncio.to_thread(mesurer)))


def surveiller_sante(annoncer: Callable[[str], None], arret: threading.Event | None = None) -> threading.Event:
    """Un fil qui mesure toutes les SANTE_INTERVALLE_MIN minutes et appelle `annoncer` pour chaque souci nouveau."""
    arret = arret or threading.Event()
    if config.SANTE_INTERVALLE_MIN <= 0:
        return arret

    def tourner() -> None:
        deja: dict[str, str] = {}
        avant: dict[str, Any] = {}
        while not arret.wait(config.SANTE_INTERVALLE_MIN * 60):
            try:
                m = mesurer()
                for texte in alertes(m, deja, avant=avant):
                    annoncer(f"Le Mac : {texte}")
                avant = m
            except Exception as e:  # une mesure ratée ne doit rien faire tomber
                print(f"   ⚠️  Santé du Mac : {type(e).__name__}: {e}", flush=True)

    threading.Thread(target=tourner, name="sante-du-mac", daemon=True).start()
    return arret


# --- La recherche dans tes fichiers ---------------------------------------------------------------------------

GENRES = {"pdf": 'kMDItemContentType == "com.adobe.pdf"', "image": 'kMDItemContentTypeTree == "public.image"',
          "video": 'kMDItemContentTypeTree == "public.movie"', "audio": 'kMDItemContentTypeTree == "public.audio"',
          "tableur": 'kMDItemContentTypeTree == "public.spreadsheet"',
          "presentation": 'kMDItemContentTypeTree == "public.presentation"',
          "texte": 'kMDItemContentTypeTree == "public.text"',
          "document": '(kMDItemContentTypeTree == "public.composite-content" || '
                      'kMDItemContentTypeTree == "public.text")',
          "dossier": 'kMDItemContentType == "public.folder"'}


def requete_spotlight(mots: str, genre: str = "", apres: str = "", avant: str = "") -> str:
    """La requête mdfind : chaque mot dans le nom OU le contenu (sans casse ni accents), le genre, les dates."""
    morceaux = []
    for mot in re.findall(r"[\w\-.']+", mots)[:8]:
        mot = mot.replace('"', "").replace("\\", "").replace("*", "")
        if len(mot) >= 2:
            morceaux.append(f'(kMDItemDisplayName == "*{mot}*"cd || kMDItemTextContent == "{mot}*"cdw)')
    if genre in GENRES:
        morceaux.append(GENRES[genre])
    for champ, valeur, signe in (("apres", apres, ">="), ("avant", avant, "<=")):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valeur or ""):
            morceaux.append(f"kMDItemContentModificationDate {signe} $time.iso({valeur})")
    return " && ".join(morceaux)


def chemin_cache(chemin: str) -> bool:
    """Les fichiers qu'il ne montre jamais : la Bibliothèque, les dossiers cachés, les secrets, les caches."""
    from .permissions import FICHIERS_SECRETS
    maison = str(Path.home())
    relatif = chemin[len(maison):] if chemin.startswith(maison) else chemin
    return (relatif.startswith("/Library/") or "/." in relatif or bool(FICHIERS_SECRETS.search(chemin))
            or "/node_modules/" in relatif)


@tool("chercher_fichiers", "Cherche dans ses fichiers, par le nom ET le contenu (Spotlight, en local) : « retrouve "
      "le PDF du devis de mars », « le document où je parle du loyer ». `mots` : les mots importants (pas « le "
      "fichier »). `genre` : pdf, document, image, tableur, presentation, video, audio, texte, dossier. `apres` / "
      "`avant` : des dates AAAA-MM-JJ (« de mars » = apres 2026-03-01, avant 2026-03-31). Puis ouvrir_fichier.",
      {"type": "object", "properties": {"mots": {"type": "string"}, "genre": {"type": "string"},
                                        "apres": {"type": "string"}, "avant": {"type": "string"}},
       "required": ["mots"]})
async def chercher_fichiers(args: dict[str, Any]) -> dict[str, Any]:
    requete = requete_spotlight(str(args.get("mots") or ""), str(args.get("genre") or "").lower(),
                                str(args.get("apres") or ""), str(args.get("avant") or ""))
    if not requete:
        return _erreur("Donne-moi au moins un mot à chercher.")
    code, sortie = await _lancer("mdfind", "-onlyin", str(Path.home()), requete, delai=20)
    if code:
        return _erreur(f"Spotlight ne répond pas ({sortie[:120]}).")
    trouves = []
    for chemin in sortie.splitlines():
        if chemin and not chemin_cache(chemin):
            try:
                trouves.append((os.path.getmtime(chemin), chemin, os.path.getsize(chemin)))
            except OSError:
                continue
    if not trouves:
        return _texte("Rien trouvé. Essaie d'autres mots, ou sans le genre ni les dates.")
    trouves.sort(reverse=True)
    maison = str(Path.home())
    lignes = [f"{datetime.fromtimestamp(t):%d/%m/%Y} · {c.replace(maison, '~', 1)} · {_taille(o)}"
              for t, c, o in trouves[:config.RECHERCHE_MAX]]
    reste = f"\n(+{len(trouves) - config.RECHERCHE_MAX} autres)" if len(trouves) > config.RECHERCHE_MAX else ""
    return _texte(f"{len(trouves)} trouvé(s), le plus récent d'abord :\n" + "\n".join(lignes) + reste)


def _taille(octets: int) -> str:
    for unite in ("o", "Ko", "Mo", "Go"):
        if octets < 1024 or unite == "Go":
            return f"{octets:.0f} {unite}" if unite == "o" else f"{octets:.1f} {unite}"
        octets /= 1024
    return ""


A_OUVRIR = {*set().union(*CATEGORIES.values())} - {".sh", ".py", ".js", ".html", ".dmg", ".pkg", ".mpkg", ".zip",
                                                    ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".xml"}


@tool("ouvrir_fichier", "Ouvre un fichier trouvé par chercher_fichiers (document, PDF, image, tableur, vidéo…) "
      "dans son app. Pas les programmes, scripts ni installeurs.",
      {"type": "object", "properties": {"chemin": {"type": "string"}}, "required": ["chemin"]})
async def ouvrir_fichier(args: dict[str, Any]) -> dict[str, Any]:
    chemin = Path(os.path.expanduser(str(args.get("chemin") or ""))).resolve()
    try:
        chemin.relative_to(Path.home().resolve())
    except ValueError:
        return _erreur("Seulement un fichier de ton dossier personnel.")
    if chemin_cache(str(chemin)):
        return _erreur("Celui-là, je ne l'ouvre pas (dossier caché, Bibliothèque ou secrets).")
    if chemin.suffix.lower() not in A_OUVRIR:
        return _erreur(f"Je n'ouvre pas les fichiers {chemin.suffix or 'sans extension'} (programmes, scripts, "
                       "installeurs) : ouvre-le toi-même si tu en es sûr.")
    if not chemin.is_file():
        return _erreur("Ce fichier n'existe pas (ou plus).")
    code, sortie = await _lancer("open", str(chemin))
    return _texte(f"Ouvert : {chemin.name}") if code == 0 else _erreur(f"Pas pu l'ouvrir ({sortie[:120]}).")


OUTILS_BUREAU = [ranger_fenetres, mode, definir_mode, musique, texte_selectionne, ranger_dossier,
                 noter_regle_rangement, annuler_rangement, sante_du_mac, chercher_fichiers, ouvrir_fichier]
