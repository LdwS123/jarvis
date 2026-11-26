"""L'atelier de code : tes projets (les dossiers qui ont un .git), leurs tests, ton standup, tes PR et ta CI sur
GitHub, les ports occupés et tes serveurs de dev. Tout tourne sur le Mac (git, gh, lsof, ton propre lanceur de
tests) : rien ne part ailleurs, sauf les questions que gh pose à GitHub pour toi.

Ce qui exécute le code d'un projet (ses tests, son serveur) ou un programme posé par Homebrew (gh) tourne dans la
cage de macOS (permissions.encager) dès qu'un texte venu d'ailleurs a été lu : le Gardien ajoute alors `cage` à
l'appel, et ce module le respecte. Ce qui prend du temps (des tests longs, une CI) ne te bloque pas : il te rend la
main et te dit la fin à voix haute (ANNONCER), avec une phrase écrite ici, jamais par le modèle.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from . import config

# Branché par __main__ : une phrase dite telle quelle (un bip, puis sa voix), même au milieu d'autre chose.
ANNONCER: Callable[[str], None] | None = None


def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _erreur(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "is_error": True}


def _annoncer(texte: str) -> None:
    if ANNONCER:
        try:
            ANNONCER(texte)
        except Exception:  # noqa: BLE001 — une annonce perdue ne doit rien casser
            pass


class Introuvable(Exception):
    """Un projet qu'on ne trouve pas, ou plusieurs qui répondent au même nom (le message dit lequel choisir)."""


# --- Lancer une commande --------------------------------------------------------------------------------------

# L'app de Steve n'a pas le PATH de ton terminal : on y ajoute là où Homebrew, Cargo, Go… posent leurs programmes.
_CHEMINS_EN_PLUS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "~/.cargo/bin", "~/go/bin",
                    "~/.bun/bin", "~/.local/bin", "~/.deno/bin")


def _env() -> dict[str, str]:
    chemins = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").split(":")
    for c in _CHEMINS_EN_PLUS:
        c = os.path.expanduser(c)
        if c not in chemins and os.path.isdir(c):
            chemins.append(c)
    # Pas de verrou git laissé derrière (la cage refuse d'écrire dans .git), pas de couleurs, jamais de question
    # posée au terminal (gh, git) : sans personne pour répondre, la commande attendrait pour rien.
    return {**os.environ, "PATH": ":".join(chemins), "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
            "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1", "CI": "1", "MPLBACKEND": "Agg"}


def binaire(nom: str) -> str | None:
    """Le chemin d'un programme (gh, npm, cargo…), y compris là où l'app de Steve ne regarde pas toute seule."""
    return shutil.which(nom, path=_env()["PATH"])


def _en_cage(cmd: list[str], cage: bool) -> list[str]:
    if not cage:
        return cmd
    from . import permissions
    return [permissions.CAGE_EXEC, "-p", permissions._cage_profil(), *cmd]


async def _lancer(*cmd: str, cwd: Path | str | None = None, delai: float = 30, cage: bool = False) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_en_cage(list(cmd), cage), cwd=str(cwd) if cwd else None, env=_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except (FileNotFoundError, PermissionError) as e:
        return 127, f"introuvable : {e.filename or cmd[0]}"
    try:
        sortie, _ = await asyncio.wait_for(proc.communicate(), delai)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "trop long, abandonné"
    return proc.returncode or 0, sortie.decode(errors="replace").strip()


def _git(depot: Path, *args: str) -> tuple[str, ...]:
    # Celui du système (un git de Homebrew se remplace depuis ton compte) ; core.fsmonitor : un programme que la
    # config d'un dépôt ferait lancer à chaque git status.
    return ("/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(depot), *args)


def _ecrire_json(fichier: Path, valeur: Any) -> None:
    fichier.parent.mkdir(parents=True, exist_ok=True)
    tmp = fichier.with_suffix(".tmp")
    tmp.write_text(json.dumps(valeur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fichier)


def _lire_json(fichier: Path, defaut: Any) -> Any:
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaut


def _fin(texte: str, n: int) -> str:
    return texte if len(texte) <= n else "…" + texte[-n:]


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def sans_couleurs(texte: str) -> str:
    return _ANSI.sub("", texte).replace("\r", "\n")


def duree(secondes: float) -> str:
    """12 s, 1 min 46, 1 h 03."""
    s = int(round(secondes))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d}" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60:02d}"


def il_y_a(horodatage: float, maintenant: float | None = None) -> str:
    ecart = (maintenant or time.time()) - horodatage
    if ecart < 90:
        return "à l'instant"
    if ecart < 3600:
        return f"il y a {int(ecart // 60)} min"
    if ecart < 86400:
        return f"il y a {int(ecart // 3600)} h"
    jours = int(ecart // 86400)
    return "hier" if jours == 1 else f"il y a {jours} jours" if jours < 30 else datetime.fromtimestamp(horodatage).strftime("le %d/%m/%Y")


# --- Tes projets ----------------------------------------------------------------------------------------------

# Là où il ne descend pas : rien que des dépendances, des médias ou le système, et c'est là que la recherche perdrait
# son temps (un node_modules contient des milliers de dossiers, parfois des .git).
_PAS_ICI = {"Library", "node_modules", "venv", "site-packages", "dist", "build", "target", "Applications",
            "Pictures", "Music", "Movies", "Public", "__pycache__", "Pods", "DerivedData", "vendor"}
_MAX_DOSSIERS = 20000
_CACHE_S = 300
_cache: tuple[float, tuple[str, ...], list[Path]] | None = None


def trouver_depots(racines: list[Path] | None = None, profondeur: int | None = None,
                   rafraichir: bool = False) -> list[Path]:
    """Les dépôts git sous tes dossiers (config.PROJETS), du plus récemment touché au plus ancien. Un dépôt trouvé,
    il ne descend pas dedans (ses sous-modules ne sont pas des projets à part). En cache cinq minutes."""
    global _cache
    racines = [Path(r).expanduser() for r in (racines if racines is not None else config.PROJETS)]
    profondeur = config.PROJETS_PROFONDEUR if profondeur is None else profondeur
    cle = tuple(str(r) for r in racines) + (str(profondeur),)
    if not rafraichir and _cache and _cache[1] == cle and time.monotonic() - _cache[0] < _CACHE_S:
        return list(_cache[2])
    trouves: list[Path] = []
    vus: set[Path] = set()
    file: deque[tuple[Path, int]] = deque((r, 0) for r in racines)
    visites = 0
    while file and visites < _MAX_DOSSIERS:
        dossier, niveau = file.popleft()
        visites += 1
        try:
            reel = dossier.resolve()
        except (OSError, RuntimeError):
            continue
        if reel in vus:
            continue
        vus.add(reel)
        if (dossier / ".git").exists():
            trouves.append(dossier)
            continue
        if niveau >= profondeur:
            continue
        try:
            enfants = sorted(dossier.iterdir())
        except OSError:
            continue
        for enfant in enfants:
            if enfant.name.startswith(".") or enfant.name in _PAS_ICI or enfant.suffix == ".app":
                continue
            try:
                if enfant.is_dir() and not enfant.is_symlink():
                    file.append((enfant, niveau + 1))
            except OSError:
                continue
    trouves.sort(key=activite, reverse=True)
    _cache = (time.monotonic(), cle, trouves)
    return list(trouves)


def activite(depot: Path) -> float:
    """Quand tu as touché ce dépôt pour la dernière fois (un git add, un commit, un changement de branche)."""
    git = depot / ".git"
    candidats = [git / "index", git / "logs" / "HEAD", git / "HEAD", git]
    dates = []
    for c in candidats:
        try:
            dates.append(c.stat().st_mtime)
        except OSError:
            pass
    return max(dates, default=0.0)


def _simple(t: str) -> str:
    """« Mon-Projet_v2 » et « mon projet v2 » se comparent pareil (à l'oral, on ne dit ni les tirets ni les accents)."""
    t = "".join(c for c in unicodedata.normalize("NFD", t.casefold()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[\s_.\-]+", "", t)


def racine_depot(chemin: Path) -> Path | None:
    for d in (chemin, *chemin.parents):
        if (d / ".git").exists():
            return d
    return None


def montrer(chemin: Path) -> str:
    """Le chemin comme tu le dis : ~/code/oko."""
    try:
        return "~/" + str(chemin.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(chemin)


def trouver_projet(nom: str | None, depots: list[Path] | None = None) -> Path:
    """Le dépôt dont tu parles : un chemin (dans ton dossier perso), ou son nom dit à l'oral (« oko », « jervis »,
    « mon site perso ») ; rien = celui sur lequel tu as travaillé en dernier."""
    nom = " ".join(str(nom or "").split())
    if nom.startswith(("/", "~")):
        chemin = Path(nom).expanduser().resolve()
        try:
            chemin.relative_to(Path.home().resolve())
        except ValueError:
            raise Introuvable("Seulement un projet de ton dossier personnel.") from None
        if not chemin.exists():
            raise Introuvable(f"{montrer(chemin)} n'existe pas.")
        racine = racine_depot(chemin if chemin.is_dir() else chemin.parent)
        if not racine:
            raise Introuvable(f"{montrer(chemin)} n'est pas dans un dépôt git.")
        return racine
    depots = trouver_depots() if depots is None else depots
    if not depots:
        raise Introuvable("Je ne trouve aucun projet (un dossier avec un .git) sous "
                          + ", ".join(montrer(p) for p in config.PROJETS) + " : règle JERVIS_PROJETS.")
    if not nom:
        return max(depots, key=activite)
    voulu = _simple(nom)
    essais: list[Callable[[Path], bool]] = [
        lambda d: d.name.casefold() == nom.casefold(),
        lambda d: _simple(d.name) == voulu,
        lambda d: _simple(d.name).startswith(voulu),
        lambda d: bool(voulu) and voulu in _simple(d.name),
    ]
    for essai in essais:
        trouves = [d for d in depots if essai(d)]
        if len(trouves) == 1:
            return trouves[0]
        if len(trouves) > 1:
            raise Introuvable(f"Plusieurs projets répondent à « {nom} » : "
                              + ", ".join(montrer(d) for d in trouves[:6]) + ". Lequel ?")
    raise Introuvable(f"Je ne trouve pas de projet « {nom} ». Ceux que tu as touchés récemment : "
                      + ", ".join(d.name for d in depots[:8]) + ".")


def _projet(args: dict[str, Any]) -> Path:
    return trouver_projet(args.get("projet"))


def lire_status(sortie: str) -> dict[str, Any]:
    """`git status --porcelain=v2 --branch` → branche, fichiers modifiés, nouveaux, en conflit, commits à pousser
    et à récupérer."""
    e = {"branche": "", "amont": "", "modifies": 0, "nouveaux": 0, "conflits": 0, "avance": 0, "retard": 0}
    for ligne in sortie.splitlines():
        if ligne.startswith("# branch.head "):
            e["branche"] = ligne.split(" ", 2)[2]
        elif ligne.startswith("# branch.upstream "):
            e["amont"] = ligne.split(" ", 2)[2]
        elif ligne.startswith("# branch.ab "):
            if m := re.match(r"# branch\.ab \+(\d+) -(\d+)", ligne):
                e["avance"], e["retard"] = int(m[1]), int(m[2])
        elif ligne.startswith(("1 ", "2 ")):
            e["modifies"] += 1
        elif ligne.startswith("u "):
            e["conflits"] += 1
        elif ligne.startswith("? "):
            e["nouveaux"] += 1
    if e["branche"] == "(detached)":
        e["branche"] = "HEAD détachée"
    return e


async def etat_depot(depot: Path) -> dict[str, Any]:
    code, sortie = await _lancer(*_git(depot, "status", "--porcelain=v2", "--branch"), delai=15)
    e = lire_status(sortie) if code == 0 else {"erreur": sortie[:160]}
    code, sortie = await _lancer(*_git(depot, "log", "-1", "--format=%ct%x1f%s"), delai=10)
    if code == 0 and "\x1f" in sortie:
        quand, sujet = sortie.split("\x1f", 1)
        e["dernier"] = (float(quand), sujet.strip())
    return {"nom": depot.name, "chemin": depot, **e}


def _pluriel(n: int, mot: str, pluriel: str | None = None) -> str:
    return f"{n} {mot if n == 1 else pluriel or mot + 's'}"


def decrire_depot(e: dict[str, Any], maintenant: float | None = None) -> str:
    """« Jervis (main) : 3 fichiers modifiés, 1 nouveau, 2 commits à pousser — dernier commit il y a 2 h : « … » »."""
    if e.get("erreur"):
        return f"{e['nom']} : git ne répond pas ({e['erreur']})"
    morceaux = []
    if e.get("conflits"):
        morceaux.append(_pluriel(e["conflits"], "fichier en conflit", "fichiers en conflit"))
    if e.get("modifies"):
        morceaux.append(_pluriel(e["modifies"], "fichier modifié", "fichiers modifiés"))
    if e.get("nouveaux"):
        morceaux.append(_pluriel(e["nouveaux"], "nouveau", "nouveaux"))
    if e.get("avance"):
        morceaux.append(_pluriel(e["avance"], "commit à pousser", "commits à pousser"))
    if e.get("retard"):
        morceaux.append(_pluriel(e["retard"], "commit à récupérer", "commits à récupérer"))
    if not e.get("amont") and e.get("branche") and e["branche"] != "HEAD détachée":
        morceaux.append("branche jamais poussée")
    ligne = f"{e['nom']} ({e.get('branche') or '?'}) : " + (", ".join(morceaux) or "propre")
    if d := e.get("dernier"):
        ligne += f" — dernier commit {il_y_a(d[0], maintenant)} : « {d[1][:90]} »"
    return ligne


async def _tous(fonction: Callable[[Path], Any], depots: list[Path], n: int = 8) -> list[Any]:
    porte = asyncio.Semaphore(n)

    async def un(d: Path) -> Any:
        async with porte:
            return await fonction(d)
    return await asyncio.gather(*(un(d) for d in depots))


@tool("lister_projets", "Où il en est dans ses projets de code (les dossiers git de son Mac, les plus récents "
      "d'abord) : branche, fichiers modifiés, commits à pousser, dernier commit. « où j'en suis dans mes projets », "
      "« qu'est-ce que j'ai pas commité ». `projet` : un seul (son nom à l'oral, ou un chemin).",
      {"type": "object", "properties": {"projet": {"type": "string"}, "tous": {"type": "boolean"}}})
async def lister_projets(args: dict[str, Any]) -> dict[str, Any]:
    try:
        depots = [_projet(args)] if args.get("projet") else trouver_depots(rafraichir=bool(args.get("tous")))
    except Introuvable as e:
        return _erreur(str(e))
    if not depots:
        return _texte("Aucun projet git trouvé sous " + ", ".join(montrer(p) for p in config.PROJETS)
                      + " (règle JERVIS_PROJETS pour chercher ailleurs).")
    montres = depots if args.get("tous") else depots[:15]
    etats = await _tous(etat_depot, montres)
    lignes = [decrire_depot(e) for e in etats]
    reste = len(depots) - len(montres)
    return _texte("\n".join(lignes) + (f"\n(+ {reste} autres projets moins récents : `tous` pour les voir)" if reste else ""))


APPS_EDITEUR = {"cursor": "Cursor", "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
                "code": "Visual Studio Code", "zed": "Zed", "xcode": "Xcode", "terminal": "Terminal",
                "iterm": "iTerm", "iterm2": "iTerm", "finder": "Finder", "sublime": "Sublime Text",
                "sublime text": "Sublime Text", "github": "GitHub"}


def url_github(origine: str) -> str | None:
    """git@github.com:moi/oko.git ou https://github.com/moi/oko.git → https://github.com/moi/oko."""
    origine = origine.strip()
    if m := re.fullmatch(r"(?:git@|ssh://git@)github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?", origine):
        return f"https://github.com/{m[1]}"
    if m := re.fullmatch(r"https://(?:[^@/]+@)?github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?", origine):
        return f"https://github.com/{m[1]}"
    return None


@tool("ouvrir_projet", "Ouvre un de ses projets de code dans son éditeur (Cursor par défaut, JERVIS_EDITEUR), ou "
      "dans `app` : Cursor, VS Code, Zed, Xcode, Terminal, iTerm, Finder, Sublime Text, ou GitHub (sa page). "
      "« ouvre Jervis », « ouvre oko dans le terminal ».",
      {"type": "object", "properties": {"projet": {"type": "string"}, "app": {"type": "string"}}})
async def ouvrir_projet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        depot = _projet(args)
    except Introuvable as e:
        return _erreur(str(e))
    voulu = " ".join(str(args.get("app") or config.EDITEUR).split()).casefold()
    app = APPS_EDITEUR.get(voulu) or next((a for a in APPS_EDITEUR.values() if a.casefold() == voulu), None)
    if not app:
        return _erreur("Je l'ouvre dans Cursor, VS Code, Zed, Xcode, Terminal, iTerm, Finder, Sublime Text ou GitHub.")
    if app == "GitHub":
        code, origine = await _lancer(*_git(depot, "remote", "get-url", "origin"), delai=10)
        url = url_github(origine) if code == 0 else None
        if not url:
            return _erreur("Ce projet n'a pas de dépôt GitHub (pas d'origin sur github.com).")
        code, _ = await _lancer("/usr/bin/open", url)
    else:
        code, _ = await _lancer("/usr/bin/open", "-a", app, str(depot))
    # (rien du dépôt ne revient ici que son nom : voir permissions._INTERNES)
    return _texte(f"C'est ouvert dans {app}.") if code == 0 else _erreur(f"{app} ne s'ouvre pas : est-elle installée ?")


# --- Les tests ------------------------------------------------------------------------------------------------

_FILTRE = re.compile(r"[\w .:/\[\]-]{1,80}")


def filtre_sur(filtre: str | None) -> str:
    """Un filtre de tests (« test_dev », « auth and not lent ») ; rien qui ressemble à une option ou au shell."""
    filtre = " ".join(str(filtre or "").split())
    if not filtre:
        return ""
    if not _FILTRE.fullmatch(filtre) or filtre.startswith("-"):
        raise ValueError("Un filtre de tests : des mots, des chiffres, « _ . : / [ ] - », sans « - » au début.")
    return filtre


def _python(depot: Path) -> str:
    for venv in (".venv", "venv", "env"):
        if (depot / venv / "bin" / "python").exists():
            return str(depot / venv / "bin" / "python")
    return binaire("python3") or "/usr/bin/python3"


def _package_json(depot: Path) -> dict[str, Any]:
    paquet = _lire_json(depot / "package.json", {})
    return paquet if isinstance(paquet, dict) else {}


def _gestionnaire_js(depot: Path) -> str:
    if (depot / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (depot / "yarn.lock").exists():
        return "yarn"
    if (depot / "bun.lockb").exists() or (depot / "bun.lock").exists():
        return "bun"
    return "npm"


def _cibles_make(depot: Path) -> set[str]:
    try:
        texte = (depot / "Makefile").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(re.findall(r"^([\w-]+)\s*:(?!=)", texte, re.MULTILINE))


def _a_pytest(depot: Path) -> bool:
    if (depot / "pytest.ini").exists() or (depot / "conftest.py").exists() or (depot / "tests" / "conftest.py").exists():
        return True
    for fichier, motif in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        try:
            if motif in (depot / fichier).read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    return any((depot / d).is_dir() and any((depot / d).glob("test_*.py")) for d in ("tests", "test"))


def commande_tests(depot: Path, filtre: str = "") -> tuple[list[str], str] | None:
    """Comment se lancent les tests de ce projet : (la commande, le lanceur). None : il ne sait pas."""
    if _a_pytest(depot):
        return [_python(depot), "-m", "pytest", "-q", *(["-k", filtre] if filtre else [])], "pytest"
    test = str(_package_json(depot).get("scripts", {}).get("test") or "")
    if test and "no test specified" not in test:
        g = _gestionnaire_js(depot)
        cmd = [g, "run", "test"] if g in ("bun", "yarn") else [g, "test"]
        if filtre:  # jest et vitest : -t ; npm et pnpm veulent « -- » avant
            cmd += ([] if g in ("bun", "yarn") else ["--"]) + ["-t", filtre]
        return cmd, g
    if (depot / "Cargo.toml").exists():
        return ["cargo", "test", *([filtre] if filtre else [])], "cargo"
    if (depot / "go.mod").exists():
        return ["go", "test", "./...", *(["-run", filtre] if filtre else [])], "go"
    if "test" in _cibles_make(depot):
        return ["make", "test"], "make"
    return None


_COMPTES = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)\b")
_ECHOUES = (re.compile(r"^FAILED (\S+)"), re.compile(r"^ERROR (\S+)"), re.compile(r"^test (\S+) \.\.\. FAILED"),
            re.compile(r"^--- FAIL: (\S+)"), re.compile(r"^\s*● (.+?)\s*$"), re.compile(r"^\s*(?:FAIL|×)\s+(\S+\.\w+)"))


def resumer_tests(sortie: str, code: int | None, secondes: float, nom: str) -> dict[str, Any]:
    """Ce que disent les tests, en chiffres, et la phrase qu'il te dira (écrite ici, jamais par le modèle)."""
    sortie = sans_couleurs(sortie)
    lignes = sortie.splitlines()
    comptes = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    resultats = [ln for ln in lignes if _COMPTES.search(ln)]
    cargo = [ln for ln in resultats if ln.strip().startswith("test result:")]
    for ln in cargo or resultats[-1:]:
        for n, quoi in _COMPTES.findall(ln):
            cle = "error" if quoi.startswith("error") else quoi
            if cle in comptes:
                comptes[cle] += int(n)
    if m := re.search(r"Tests:\s+(.+)", sortie):  # jest
        for n, quoi in re.findall(r"(\d+) (passed|failed|skipped)", m[1]):
            comptes[quoi] = int(n)
    echoues: list[str] = []
    for ln in lignes:
        for motif in _ECHOUES:
            if (m := motif.match(ln)) and m[1] not in echoues:
                echoues.append(m[1][:120])
    ok = code == 0
    rates = comptes["failed"] + comptes["error"]
    temps = duree(secondes)
    if code is None:
        phrase = f"Les tests de {nom} tournaient encore après {temps} : je les ai arrêtés."
    elif ok:
        phrase = (f"Les tests de {nom} sont passés : {_pluriel(comptes['passed'], 'réussi')}, en {temps}."
                  if comptes["passed"] else f"Les tests de {nom} sont passés, en {temps}.")
    else:
        total = comptes["passed"] + rates
        phrase = (f"Les tests de {nom} ont échoué : {rates} en échec sur {total}, en {temps}."
                  if rates else f"Les tests de {nom} ont échoué (code {code}), en {temps}.")
        phrase += " Dis-moi « corrige » et je regarde."
    return {"ok": ok, "code": code, "duree": round(secondes, 1), **comptes, "echoues": echoues[:10], "phrase": phrase,
            "fini": time.time()}


_TESTS_EN_COURS: dict[str, asyncio.Task] = {}


def _journal_tests(depot: Path) -> Path:
    return config.DOSSIER_DEV / f"tests-{_simple(depot.name) or 'projet'}.log"


async def _executer_tests(depot: Path, cmd: list[str], cage: bool) -> dict[str, Any]:
    journal = _journal_tests(depot)
    journal.parent.mkdir(parents=True, exist_ok=True)
    debut = time.monotonic()
    with open(journal, "w", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}   (dans {montrer(depot)}{', dans la cage' if cage else ''})\n")
        f.flush()
        try:
            proc = await asyncio.create_subprocess_exec(
                *_en_cage(cmd, cage), cwd=str(depot), env=_env(), stdin=asyncio.subprocess.DEVNULL,
                stdout=f, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except (FileNotFoundError, PermissionError) as e:
            f.write(f"\nintrouvable : {e.filename or cmd[0]}\n")
            code: int | None = 127
        else:
            try:
                code = await asyncio.wait_for(proc.wait(), config.TESTS_MAX_MIN * 60)
            except asyncio.TimeoutError:
                code = None
                _arreter_groupe(proc.pid)
                await proc.wait()
            except asyncio.CancelledError:
                _arreter_groupe(proc.pid)
                raise
    sortie = journal.read_text(encoding="utf-8", errors="replace")
    resultat = {**resumer_tests(sortie, code, time.monotonic() - debut, depot.name), "projet": str(depot),
                "commande": cmd}
    _ecrire_json(journal.with_suffix(".json"), resultat)
    return resultat


def _texte_resultat(r: dict[str, Any]) -> str:
    texte = r["phrase"]
    if r["echoues"]:
        texte += "\nEn échec : " + ", ".join(r["echoues"])
    if not r["ok"]:
        texte += "\n(lister_resultats_tests donne la sortie, pour corriger)"
    return texte


@tool("lancer_tests", "Lance les tests d'un de ses projets (pytest, npm/pnpm/yarn/bun, cargo, go ou make test, "
      "trouvé tout seul) : « lance les tests de Jervis », « relance juste test_dev ». S'ils durent, il lui rend la "
      "main et lui dit le résultat à voix haute quand c'est fini. `filtre` : quels tests (pytest -k, jest -t…).",
      {"type": "object", "properties": {"projet": {"type": "string"}, "filtre": {"type": "string"}}})
async def lancer_tests(args: dict[str, Any]) -> dict[str, Any]:
    try:
        depot = _projet(args)
        filtre = filtre_sur(args.get("filtre"))
    except (Introuvable, ValueError) as e:
        return _erreur(str(e))
    trouve = commande_tests(depot, filtre)
    if not trouve:
        return _erreur(f"Je ne sais pas lancer les tests de {depot.name} (ni pytest, ni script test dans package.json, "
                       "ni Cargo, ni Go, ni « make test ») : dis-moi la commande.")
    cmd, _lanceur = trouve
    cle = str(depot.resolve())
    if (deja := _TESTS_EN_COURS.get(cle)) and not deja.done():
        return _texte(f"Les tests de {depot.name} tournent déjà : je te dis quand c'est fini.")
    tache = asyncio.create_task(_executer_tests(depot, cmd, cage=args.get("cage") is True))
    _TESTS_EN_COURS[cle] = tache
    fini, _ = await asyncio.wait({tache}, timeout=config.TESTS_ATTENTE_S)
    if tache in fini:
        return _texte(_texte_resultat(tache.result()))

    def a_la_fin(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is None:
            _annoncer(t.result()["phrase"])
        elif not t.cancelled():
            _annoncer(f"Les tests de {depot.name} n'ont pas pu aller au bout.")
    tache.add_done_callback(a_la_fin)
    return _texte(f"Les tests de {depot.name} tournent encore ({' '.join(cmd[-3:])}…) : je te le dis dès que c'est "
                  "fini. Tu peux me demander autre chose en attendant.")


@tool("lister_resultats_tests", "Le dernier résultat des tests d'un projet, avec la fin de leur sortie (les "
      "erreurs) : pour savoir quoi corriger après « les tests ont échoué », ou « où en sont les tests ».",
      {"type": "object", "properties": {"projet": {"type": "string"}}})
async def lister_resultats_tests(args: dict[str, Any]) -> dict[str, Any]:
    try:
        depot = _projet(args)
    except Introuvable as e:
        return _erreur(str(e))
    journal = _journal_tests(depot)
    en_cours = (t := _TESTS_EN_COURS.get(str(depot.resolve()))) is not None and not t.done()
    try:
        sortie = sans_couleurs(journal.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return _texte(f"Pas encore de tests lancés par moi pour {depot.name} : dis « lance les tests ».")
    r = _lire_json(journal.with_suffix(".json"), {})
    tete = (f"Les tests de {depot.name} tournent encore. Pour l'instant :" if en_cours else
            f"{r.get('phrase', 'Derniers tests')} ({il_y_a(r['fini'])})" if r.get("fini") else "Derniers tests :")
    if not en_cours and r.get("echoues"):
        tete += "\nEn échec : " + ", ".join(r["echoues"])
    return _texte(f"{tete}\n--- fin de la sortie ({montrer(journal)}) ---\n{_fin(sortie, 6000)}")


# --- Ton standup ----------------------------------------------------------------------------------------------

def depuis_quand(depuis: str | None, maintenant: datetime | None = None) -> datetime:
    """aujourd'hui, hier (par défaut : ton standup), semaine, ou une date AAAA-MM-JJ → minuit ce jour-là."""
    maintenant = maintenant or datetime.now()
    minuit = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    mot = re.sub(r"\W", "", _simple(depuis or "hier"))  # « aujourd'hui », avec ou sans apostrophe
    if mot in ("aujourdhui", "ajd", "today"):
        return minuit
    if mot in ("hier", "yesterday"):
        return minuit - timedelta(days=3 if minuit.weekday() == 0 else 1)  # le lundi : depuis vendredi
    if mot in ("semaine", "cettesemaine", "week", "7jours"):
        return minuit - timedelta(days=7)
    try:
        return datetime.strptime(str(depuis).strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError("`depuis` : aujourd'hui, hier, semaine ou une date AAAA-MM-JJ.") from None


def est_worktree(depot: Path) -> bool:
    """Un git worktree (son .git est un fichier) : il partage ses commits avec le dépôt d'origine."""
    return (depot / ".git").is_file()


async def commits_depuis(depot: Path, depuis: datetime) -> list[tuple[str, float, str]]:
    """Tes commits (même adresse que user.email) depuis `depuis` : (empreinte, date, sujet). Toutes les branches
    locales pour un dépôt, seulement la sienne pour un worktree (les autres sont celles du dépôt d'origine)."""
    code, moi = await _lancer(*_git(depot, "config", "user.email"), delai=10)
    if code != 0 or not moi.strip():
        return []
    code, sortie = await _lancer(*_git(depot, "log", "HEAD" if est_worktree(depot) else "--branches", "--no-merges",
                                       f"--since={depuis.isoformat()}", f"--author={moi.strip()}",
                                       "--format=%H%x1f%ct%x1f%s"), delai=20)
    commits = []
    for ligne in sortie.splitlines() if code == 0 else []:
        if ligne.count("\x1f") >= 2:
            empreinte, quand, sujet = ligne.split("\x1f", 2)
            commits.append((empreinte, float(quand), sujet.strip()))
    return commits


@tool("lister_commits", "Ce qu'il a fait dans ses projets (ses commits, tous dépôts confondus, et ce qui n'est pas "
      "encore commité) : « fais mon standup », « qu'est-ce que j'ai fait cette semaine ». `depuis` : aujourd'hui, "
      "hier (par défaut, depuis vendredi le lundi), semaine, ou AAAA-MM-JJ.",
      {"type": "object", "properties": {"depuis": {"type": "string"}, "projet": {"type": "string"}}})
async def lister_commits(args: dict[str, Any]) -> dict[str, Any]:
    try:
        depuis = depuis_quand(args.get("depuis"))
        depots = [_projet(args)] if args.get("projet") else trouver_depots()
    except (Introuvable, ValueError) as e:
        return _erreur(str(e))
    limite = depuis.timestamp()
    recents = [d for d in depots if activite(d) >= limite - 86400] if not args.get("projet") else depots
    # Les worktrees d'abord : un commit fait dans l'un d'eux compte là, pas une deuxième fois dans le dépôt d'origine.
    recents.sort(key=lambda d: not est_worktree(d))

    async def bilan(d: Path) -> tuple[Path, list[tuple[float, str]], dict[str, Any]]:
        return d, await commits_depuis(d, depuis), await etat_depot(d)
    blocs = []
    total = 0
    vus: set[str] = set()
    for depot, tous_les_commits, e in await _tous(bilan, recents):
        commits = [(quand, sujet) for empreinte, quand, sujet in tous_les_commits if empreinte not in vus]
        vus.update(empreinte for empreinte, _, _ in tous_les_commits)
        en_cours = e.get("modifies", 0) + e.get("nouveaux", 0)
        if not commits and not en_cours:
            continue
        total += len(commits)
        tete = f"{depot.name} ({e.get('branche') or '?'}) : {_pluriel(len(commits), 'commit')}"
        if en_cours:
            tete += f", et {_pluriel(en_cours, 'fichier')} pas encore commité{'s' if en_cours > 1 else ''}"
        lignes = [f"  • {sujet[:100]} ({il_y_a(quand)})" for quand, sujet in sorted(commits, reverse=True)[:12]]
        if len(commits) > 12:
            lignes.append(f"  • … et {len(commits) - 12} autres")
        blocs.append("\n".join([tete, *lignes]))
    if not blocs:
        return _texte(f"Aucun commit de toi depuis le {depuis:%d/%m}, et rien en cours.")
    return _texte(f"Depuis le {depuis:%d/%m} : {_pluriel(total, 'commit')}.\n" + "\n".join(blocs))


# --- GitHub (par gh) ------------------------------------------------------------------------------------------

def _gh() -> str | None:
    return binaire("gh")


_GH_ABSENT = ("gh (l'outil GitHub) n'est pas installé : « brew install gh », puis « gh auth login » dans ton terminal.")
_GH_PAS_CONNECTE = "gh n'est pas connecté à ton compte GitHub : lance « gh auth login » dans ton terminal."


async def _gh_json(*args: str, cwd: Path | None = None, cage: bool = False) -> Any:
    """La réponse de gh (--json), ou ValueError avec ce qu'il faut te dire."""
    gh = _gh()
    if not gh:
        raise ValueError(_GH_ABSENT)
    code, sortie = await _lancer(gh, *args, cwd=cwd, delai=40, cage=cage)
    if code != 0:
        if re.search(r"gh auth login|not logged|authentication", sortie, re.IGNORECASE):
            raise ValueError(_GH_PAS_CONNECTE)
        if re.search(r"no git remotes|not a git repository|could not determine|none of the git remotes", sortie,
                     re.IGNORECASE):
            raise ValueError("Ce projet n'est pas relié à un dépôt GitHub.")
        raise ValueError(f"gh a répondu : {sortie[:200]}")
    try:
        return json.loads(sortie or "null")
    except ValueError:
        raise ValueError(f"gh a répondu autre chose que prévu : {sortie[:160]}") from None


_ROUGE = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def etat_ci(controles: list[dict[str, Any]] | None) -> str:
    """Le résumé des contrôles d'une PR (statusCheckRollup) : ❌ un en échec, ⏳ en cours, ✅ tous verts, "" aucun."""
    if not controles:
        return ""
    if any(str(c.get("conclusion") or c.get("state") or "").upper() in _ROUGE for c in controles):
        return "❌"
    if any(str(c.get("status") or "").upper() not in ("", "COMPLETED")
           or str(c.get("state") or "").upper() in ("PENDING", "EXPECTED") for c in controles):
        return "⏳"
    return "✅"


def _ligne_pr(pr: dict[str, Any], avec_depot: bool = False) -> str:
    depot = (pr.get("repository") or {}).get("nameWithOwner") or (pr.get("repository") or {}).get("name") or ""
    ci = etat_ci(pr.get("statusCheckRollup"))
    avis = {"APPROVED": " · approuvée", "CHANGES_REQUESTED": " · des changements demandés",
            "REVIEW_REQUIRED": " · en attente de relecture"}.get(str(pr.get("reviewDecision") or ""), "")
    qui = (pr.get("author") or {}).get("login")
    return (f"  • {ci + ' ' if ci else ''}#{pr.get('number')} {str(pr.get('title') or '')[:90]}"
            + (f" ({depot})" if avec_depot and depot else "") + (f" — de {qui}" if qui else "")
            + (" · brouillon" if pr.get("isDraft") else "") + avis + f"\n    {pr.get('url', '')}")


def _ligne_run(r: dict[str, Any]) -> str:
    etat = (f"❌ {str(r.get('conclusion')).lower()}" if str(r.get("conclusion") or "").upper() in _ROUGE else
            "✅" if str(r.get("conclusion") or "").lower() == "success" else
            f"⏳ {str(r.get('status') or '').lower()}" if str(r.get("status") or "").lower() != "completed" else
            str(r.get("conclusion") or "").lower())
    return f"  • {etat} {r.get('workflowName') or r.get('name') or '?'} — {str(r.get('displayTitle') or '')[:80]}"


async def _branche(depot: Path) -> str:
    code, sortie = await _lancer(*_git(depot, "branch", "--show-current"), delai=10)
    return sortie.strip() if code == 0 else ""


@tool("lister_activite_github", "Son GitHub : ses PR ouvertes (avec l'état de la CI ✅ ❌ ⏳ et des relectures), les PR "
      "qu'on lui demande de relire, et avec un `projet` les dernières CI de sa branche (et pourquoi la dernière a "
      "échoué). « mes PR », « qu'est-ce que je dois relire », « pourquoi la CI a planté ».",
      {"type": "object", "properties": {"projet": {"type": "string"}}})
async def lister_activite_github(args: dict[str, Any]) -> dict[str, Any]:
    cage = args.get("cage") is True
    champs = "number,title,url,isDraft,reviewDecision,statusCheckRollup,headRefName"
    try:
        if not args.get("projet"):
            miennes = await _gh_json("search", "prs", "--author", "@me", "--state", "open", "--limit", "30",
                                     "--json", "number,title,url,repository,isDraft", cage=cage)
            a_relire = await _gh_json("search", "prs", "--review-requested", "@me", "--state", "open", "--limit", "30",
                                      "--json", "number,title,url,repository,author", cage=cage)
            parties = [f"Tes PR ouvertes ({len(miennes)}) :" if miennes else "Aucune PR ouverte de toi.",
                       *(_ligne_pr(p, True) for p in miennes),
                       f"À relire pour toi ({len(a_relire)}) :" if a_relire else "Personne ne t'attend pour une relecture.",
                       *(_ligne_pr(p, True) for p in a_relire),
                       "(avec un projet, je te donne aussi l'état de sa CI)"]
            return _texte("\n".join(parties))
        depot = _projet(args)
        miennes = await _gh_json("pr", "list", "--author", "@me", "--state", "open", "--json", champs, cwd=depot, cage=cage)
        a_relire = await _gh_json("pr", "list", "--search", "review-requested:@me", "--state", "open",
                                  "--json", "number,title,url,author", cwd=depot, cage=cage)
        branche = await _branche(depot)
        runs = await _gh_json("run", "list", "--limit", "6", *(["--branch", branche] if branche else []), "--json",
                              "databaseId,workflowName,name,status,conclusion,displayTitle,headSha", cwd=depot, cage=cage)
    except (Introuvable, ValueError) as e:
        return _erreur(str(e))
    parties = [f"{depot.name} — tes PR ouvertes ({len(miennes)}) :" if miennes else f"{depot.name} : aucune PR ouverte de toi.",
               *(_ligne_pr(p) for p in miennes)]
    if a_relire:
        parties += [f"À relire pour toi ({len(a_relire)}) :", *(_ligne_pr(p) for p in a_relire)]
    if runs:
        parties += [f"Dernières CI sur {branche or 'le dépôt'} :", *(_ligne_run(r) for r in runs)]
        dernier = runs[0]
        if str(dernier.get("conclusion") or "").upper() in _ROUGE and dernier.get("databaseId"):
            gh = _gh() or "gh"
            code, journal = await _lancer(gh, "run", "view", str(dernier["databaseId"]), "--log-failed",
                                          cwd=depot, delai=60, cage=cage)
            if code == 0 and journal:
                parties += ["--- pourquoi la dernière a échoué (fin du journal) ---", _fin(sans_couleurs(journal), 3500)]
    else:
        parties.append("Aucune CI lancée sur cette branche.")
    return _texte("\n".join(parties))


_SURVEILLANCES: dict[tuple[str, str], asyncio.Task] = {}
_dormir = asyncio.sleep  # (remplacé dans les tests)
_PAS_S = 20
_APPARITION_S = 120


def bilan_ci(runs: list[dict[str, Any]], nom: str) -> str | None:
    """La phrase à dire quand toutes les CI d'un commit sont finies ; None tant qu'il en tourne une."""
    if not runs or any(str(r.get("status") or "").lower() != "completed" for r in runs):
        return None
    noms = lambda rs: ", ".join(sorted({str(r.get("workflowName") or r.get("name") or "?") for r in rs}))  # noqa: E731
    rates = [r for r in runs if str(r.get("conclusion") or "").upper() in _ROUGE]
    if rates:
        return (f"La CI de {nom} a échoué : {noms(rates)}. Dis-moi « pourquoi la CI a planté » et je regarde.")
    return f"La CI de {nom} est passée ({noms(runs)})."


async def _runs_du_commit(depot: Path, sha: str, cage: bool) -> list[dict[str, Any]]:
    return await _gh_json("run", "list", "--commit", sha, "--limit", "30", "--json",
                          "databaseId,workflowName,name,status,conclusion", cwd=depot, cage=cage) or []


async def _surveiller(depot: Path, sha: str, cage: bool) -> None:
    debut = time.monotonic()
    fin = debut + config.CI_MAX_MIN * 60
    while time.monotonic() < fin:
        await _dormir(_PAS_S)
        try:
            runs = await _runs_du_commit(depot, sha, cage)
        except ValueError:
            continue  # le réseau qui flanche : on réessaie au tour suivant
        if not runs and time.monotonic() - debut > _APPARITION_S:
            _annoncer(f"Aucune CI ne s'est lancée pour ton dernier commit de {depot.name}.")
            return
        if phrase := bilan_ci(runs, depot.name):
            _annoncer(phrase)
            return
    _annoncer(f"La CI de {depot.name} tourne encore après {duree(config.CI_MAX_MIN * 60)} : j'arrête de la surveiller.")


@tool("surveiller_ci", "Surveille la CI GitHub du dernier commit d'un projet et lui dit à voix haute quand elle est "
      "finie (passée, ou ce qui a échoué) : « préviens-moi quand la CI est finie ». Il lui rend la main tout de suite.",
      {"type": "object", "properties": {"projet": {"type": "string"}}})
async def surveiller_ci(args: dict[str, Any]) -> dict[str, Any]:
    cage = args.get("cage") is True
    try:
        depot = _projet(args)
    except Introuvable as e:
        return _erreur(str(e))
    code, sha = await _lancer(*_git(depot, "rev-parse", "HEAD"), delai=10)
    if code != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha.strip()):
        return _erreur(f"{depot.name} n'a pas encore de commit.")
    sha = sha.strip()
    e = await etat_depot(depot)
    if e.get("avance") or not e.get("amont"):
        return _texte(f"Ton dernier commit de {depot.name} n'est pas encore poussé : pas de CI à surveiller. "
                      "Pousse-le, puis redemande-moi.")
    cle = (str(depot.resolve()), sha)
    if (deja := _SURVEILLANCES.get(cle)) and not deja.done():
        return _texte(f"Je surveille déjà la CI de {depot.name} : je te dis dès que c'est fini.")
    try:
        runs = await _runs_du_commit(depot, sha, cage)
    except ValueError as err:
        return _erreur(str(err))
    if phrase := bilan_ci(runs, depot.name):
        return _texte(f"C'est déjà fini : {phrase}")
    _SURVEILLANCES[cle] = asyncio.create_task(_surveiller(depot, sha, cage))
    en_cours = ", ".join(sorted({str(r.get("workflowName") or "?") for r in runs})) if runs else "pas encore lancée"
    return _texte(f"Je surveille la CI de {depot.name} (commit {sha[:7]}, {en_cours}) : je te dis dès que c'est fini.")


# --- Les ports ------------------------------------------------------------------------------------------------

def lire_lsof(sortie: str) -> list[dict[str, Any]]:
    """`lsof -nP -iTCP -sTCP:LISTEN -F pcn` → [{pid, commande, port}], un par (programme, port)."""
    ecoutes: list[dict[str, Any]] = []
    pid, commande = 0, ""
    vus: set[tuple[int, int]] = set()
    for ligne in sortie.splitlines():
        if not ligne:
            continue
        champ, valeur = ligne[0], ligne[1:]
        if champ == "p":
            pid, commande = int(valeur) if valeur.isdigit() else 0, ""
        elif champ == "c":
            commande = valeur
        elif champ == "n" and (m := re.search(r":(\d+)$", valeur)):
            port = int(m[1])
            if pid and (pid, port) not in vus:
                vus.add((pid, port))
                ecoutes.append({"pid": pid, "commande": commande, "port": port, "adresse": valeur})
    return sorted(ecoutes, key=lambda e: (e["port"], e["pid"]))


async def _ecoutes() -> list[dict[str, Any]]:
    code, sortie = await _lancer("/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn", delai=20)
    return lire_lsof(sortie) if sortie else []


async def _details(pids: list[int]) -> dict[int, dict[str, str]]:
    """Le programme (son chemin) et le dossier de travail de chaque pid : pour dire à quel projet il appartient."""
    if not pids:
        return {}
    liste = ",".join(str(p) for p in sorted(set(pids)))
    infos: dict[int, dict[str, str]] = {p: {} for p in pids}
    code, sortie = await _lancer("/bin/ps", "-o", "pid=,comm=", "-p", liste, delai=10)
    for ligne in sortie.splitlines():
        if m := re.match(r"\s*(\d+)\s+(.+)$", ligne):
            infos.setdefault(int(m[1]), {})["chemin"] = m[2].strip()
    code, sortie = await _lancer("/usr/sbin/lsof", "-a", "-p", liste, "-d", "cwd", "-F", "pn", delai=10)
    pid = 0
    for ligne in sortie.splitlines():
        if ligne.startswith("p") and ligne[1:].isdigit():
            pid = int(ligne[1:])
        elif ligne.startswith("n") and pid:
            infos.setdefault(pid, {})["dossier"] = ligne[1:]
    return infos


_AIRPLAY = ("C'est le récepteur AirPlay de macOS (ControlCenter) : coupe-le dans Réglages → Général → AirDrop et "
            "Handoff → Récepteur AirPlay, ou lance ton serveur sur un autre port.")


def _projet_du_dossier(dossier: str) -> str:
    if not dossier or dossier == "/":
        return ""
    racine = racine_depot(Path(dossier))
    return racine.name if racine and racine != Path.home() else ""


@tool("lister_ports", "Ce qui écoute sur les ports de son Mac (serveurs de dev, bases de données…) : le port, le "
      "programme, son projet. « qu'est-ce qui tourne sur le 3000 », « mes serveurs ». `port` : un seul.",
      {"type": "object", "properties": {"port": {"type": "integer"}}})
async def lister_ports(args: dict[str, Any]) -> dict[str, Any]:
    ecoutes = [e for e in await _ecoutes() if e["port"] < 49152]  # au-delà : les ports éphémères du système
    if args.get("port"):
        ecoutes = [e for e in ecoutes if e["port"] == int(args["port"])]
        if not ecoutes:
            return _texte(f"Rien n'écoute sur le port {int(args['port'])} : il est libre.")
    if not ecoutes:
        return _texte("Aucun serveur n'écoute sur ton Mac en ce moment.")
    infos = await _details([e["pid"] for e in ecoutes])
    lignes = []
    for e in ecoutes:
        i = infos.get(e["pid"], {})
        projet = _projet_du_dossier(i.get("dossier", ""))
        ligne = f":{e['port']} — {e['commande']} (pid {e['pid']})" + (f", projet {projet}" if projet else "")
        if e["pid"] == os.getpid():
            ligne += " — c'est moi (mon tableau de bord)"
        elif e["commande"].startswith("ControlCe") and e["port"] in (5000, 7000):
            ligne += " — " + _AIRPLAY
        lignes.append(ligne)
    return _texte("\n".join(lignes))


def _vivant(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def _arreter_groupe(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def refus_port(port: int, ecoute: dict[str, Any], chemin: str) -> str | None:
    """Pourquoi il ne libère pas ce port (lui-même, le système, une app) ; None : il peut."""
    if ecoute["pid"] in (os.getpid(), os.getppid()) or port == config.TABLEAU_PORT:
        return f"Le port {port}, c'est moi (mon tableau de bord) : je ne m'arrête pas moi-même."
    if ecoute["commande"].startswith("ControlCe"):
        return _AIRPLAY
    if chemin.startswith(("/System/", "/usr/libexec/", "/usr/sbin/", "/sbin/")):
        return f"Le port {port} est tenu par macOS ({ecoute['commande']}) : je n'y touche pas."
    if m := re.search(r"/([^/]+)\.app/Contents/MacOS/", chemin):
        return f"Le port {port} est tenu par l'app {m[1]} : quitte-la plutôt (ou change son réglage de port)."
    return None


@tool("liberer_port", "Libère un port occupé (« libère le 3000 », « tue ce qui tourne sur le 8000 ») : arrête "
      "proprement le programme qui l'écoute (SIGTERM). S'il résiste, `forcer` le coupe net. Jamais une app, macOS "
      "ni lui-même.",
      {"type": "object", "properties": {"port": {"type": "integer"}, "forcer": {"type": "boolean"}},
       "required": ["port"]})
async def liberer_port(args: dict[str, Any]) -> dict[str, Any]:
    try:
        port = int(args.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        return _erreur("Un port, c'est un nombre entre 1 et 65535.")
    ecoutes = [e for e in await _ecoutes() if e["port"] == port]
    if not ecoutes:
        return _texte(f"Rien n'écoute sur le port {port} : il est déjà libre.")
    infos = await _details([e["pid"] for e in ecoutes])
    for e in ecoutes:
        if motif := refus_port(port, e, infos.get(e["pid"], {}).get("chemin", "")):
            return _erreur(motif)
    arretes, resistent = [], []
    for e in ecoutes:
        try:
            _signal(e["pid"], signal.SIGTERM)
        except ProcessLookupError:
            arretes.append(e)
            continue
        except PermissionError:
            return _erreur(f"{e['commande']} (pid {e['pid']}) n'est pas à toi : je ne peux pas l'arrêter.")
        for _ in range(30):
            if not _vivant(e["pid"]):
                break
            await asyncio.sleep(0.1)
        if _vivant(e["pid"]) and args.get("forcer"):
            _signal(e["pid"], signal.SIGKILL)
            await asyncio.sleep(0.3)
        (resistent if _vivant(e["pid"]) else arretes).append(e)
    if resistent:
        return _texte(f"{', '.join(e['commande'] for e in resistent)} résiste sur le port {port} : redis-moi de le "
                      "forcer, et je le coupe net.")
    return _texte(f"Le port {port} est libre (j'ai arrêté {', '.join(sorted({e['commande'] for e in arretes}))}).")


# --- Tes serveurs de dev --------------------------------------------------------------------------------------

def commande_serveur(depot: Path) -> list[str] | None:
    """Comment se lance le serveur de dev de ce projet ; None : il ne sait pas."""
    scripts = _package_json(depot).get("scripts", {})
    for script in ("dev", "start", "serve"):
        if isinstance(scripts, dict) and scripts.get(script):
            return [_gestionnaire_js(depot), "run", script]
    if (depot / "manage.py").exists():
        return [_python(depot), "manage.py", "runserver"]
    cibles = _cibles_make(depot)
    for cible in ("dev", "run", "serve"):
        if cible in cibles:
            return ["make", cible]
    if (depot / "bin" / "rails").exists():
        return ["bin/rails", "server"]
    return None


_URL_LOCALE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::\d{2,5})?(?:/[^\s'\"<>]*)?")


def url_locale(sortie: str) -> str | None:
    """La première adresse locale qu'un serveur annonce (« Local: http://localhost:5173/ »)."""
    if m := _URL_LOCALE.search(sans_couleurs(sortie)):
        return re.sub(r"//(?:0\.0\.0\.0|\[::1?\])", "//localhost", m[0]).rstrip(".,;)")
    return None


def _fichier_serveurs() -> Path:
    return config.DOSSIER_DEV / "serveurs.json"


def serveurs() -> dict[str, dict[str, Any]]:
    """Les serveurs qu'il a lancés et qui tournent encore (les autres sont oubliés)."""
    tous = _lire_json(_fichier_serveurs(), {})
    vivants = {k: v for k, v in (tous.items() if isinstance(tous, dict) else []) if _groupe_vivant(int(v.get("pid", 0)))}
    if vivants != tous:
        _ecrire_json(_fichier_serveurs(), vivants)
    return vivants


def _groupe_vivant(pid: int) -> bool:
    """Le serveur qu'il a lancé (chef de son groupe), pas un autre programme qui aurait repris son numéro."""
    if pid <= 1:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:  # un de ses enfants, fini : on le ramasse
            return False
    except ChildProcessError:
        pass
    if not _vivant(pid):
        return False
    try:
        return os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError):
        return False


def _lancer_serveur(depot: Path, cmd: list[str], journal: Path, cage: bool) -> int:
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "w", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}   (dans {montrer(depot)}{', dans la cage' if cage else ''})\n")
        f.flush()
        proc = subprocess.Popen(_en_cage(cmd, cage), cwd=str(depot), env=_env(), stdin=subprocess.DEVNULL,
                                stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return proc.pid


async def _attendre_url(pid: int, journal: Path, secondes: float = 20) -> tuple[str | None, bool]:
    """(l'adresse annoncée, le serveur tourne-t-il encore)."""
    fin = time.monotonic() + secondes
    while time.monotonic() < fin:
        try:
            texte = journal.read_text(encoding="utf-8", errors="replace")
        except OSError:
            texte = ""
        if url := url_locale(texte.split("\n", 1)[-1]):
            return url, True
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return None, False
        except ChildProcessError:
            if not _vivant(pid):
                return None, False
        await asyncio.sleep(0.4)
    return None, _vivant(pid)


@tool("serveur_de_dev", "Le serveur de dev d'un projet (npm/pnpm run dev, Django, make dev, Rails) : `action` = "
      "lancer (il trouve l'adresse, et l'ouvre dans le navigateur avec `ouvrir`), arreter, ou etat (ceux qui "
      "tournent). « lance le serveur de oko et ouvre-le », « arrête le serveur ».",
      {"type": "object", "properties": {"projet": {"type": "string"},
                                        "action": {"type": "string", "enum": ["lancer", "arreter", "etat"]},
                                        "ouvrir": {"type": "boolean"}}})
async def serveur_de_dev(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "lancer")
    cage = args.get("cage") is True
    actifs = serveurs()
    if action == "etat" or (action == "arreter" and not args.get("projet")):
        if not actifs:
            return _texte("Aucun serveur de dev lancé par moi ne tourne.")
        if action == "arreter" and len(actifs) > 1:
            return _erreur("Plusieurs serveurs tournent (" + ", ".join(Path(k).name for k in actifs) + ") : lequel ?")
        if action == "etat":
            return _texte("\n".join(f"{Path(k).name} : {v.get('url') or 'adresse pas encore vue'} (pid {v['pid']}, "
                                    f"depuis {il_y_a(v.get('depuis', time.time()))})" for k, v in actifs.items()))
        depot = Path(next(iter(actifs)))
    else:
        try:
            depot = _projet(args)
        except Introuvable as e:
            return _erreur(str(e))
    cle = str(depot.resolve())
    if action == "arreter":
        if cle not in actifs:
            return _texte(f"Je n'ai pas de serveur de {depot.name} qui tourne (s'il occupe un port, « libère le "
                          "port … »).")
        pid = int(actifs[cle]["pid"])
        _arreter_groupe(pid)
        for _ in range(30):
            if not _groupe_vivant(pid):
                break
            await asyncio.sleep(0.1)
        if _groupe_vivant(pid):
            _arreter_groupe(pid, signal.SIGKILL)
        actifs.pop(cle, None)
        _ecrire_json(_fichier_serveurs(), actifs)
        return _texte(f"Le serveur de {depot.name} est arrêté.")
    if action != "lancer":
        return _erreur("`action` : lancer, arreter ou etat.")
    if cle in actifs:
        url = actifs[cle].get("url")
        return _texte(f"Le serveur de {depot.name} tourne déjà" + (f" : {url}" if url else "") + ".")
    cmd = commande_serveur(depot)
    if not cmd:
        return _erreur(f"Je ne sais pas lancer le serveur de {depot.name} (ni script dev/start dans package.json, ni "
                       "manage.py, ni make dev, ni Rails) : dis-moi la commande.")
    journal = config.DOSSIER_DEV / f"serveur-{_simple(depot.name) or 'projet'}.log"
    try:
        pid = _lancer_serveur(depot, cmd, journal, cage)
    except (FileNotFoundError, PermissionError) as e:
        return _erreur(f"{e.filename or cmd[0]} est introuvable : est-il installé ?")
    url, vivant = await _attendre_url(pid, journal)
    sortie = sans_couleurs(journal.read_text(encoding="utf-8", errors="replace")) if journal.exists() else ""
    if not vivant:
        return _erreur(f"Le serveur de {depot.name} s'est arrêté tout de suite. Fin de sa sortie :\n{_fin(sortie, 2500)}")
    actifs[cle] = {"pid": pid, "commande": cmd, "url": url, "journal": str(journal), "depuis": time.time()}
    _ecrire_json(_fichier_serveurs(), actifs)
    texte = f"Le serveur de {depot.name} tourne ({' '.join(cmd)})" + (f" : {url}" if url else
                                                                       ", mais je n'ai pas encore vu son adresse")
    if url and args.get("ouvrir"):
        if cage:  # la cage ferme LaunchServices : et ce n'est pas à un texte venu d'ailleurs d'ouvrir une page
            texte += ". Je ne l'ouvre pas moi-même juste après un texte venu d'ailleurs : clique sur le lien."
        else:
            code, _ = await _lancer("/usr/bin/open", url)
            texte += ", ouvert dans ton navigateur" if code == 0 else ""
    return _texte(texte + f".\n--- sa sortie ---\n{_fin(sortie, 1200)}")


OUTILS_DEV = [lister_projets, ouvrir_projet, lancer_tests, lister_resultats_tests, lister_commits,
              lister_activite_github, surveiller_ci, lister_ports, liberer_port, serveur_de_dev]
