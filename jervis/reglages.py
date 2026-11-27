"""La page Réglages : qui clone le dépôt y choisit son assistant (son nom, ses langues, sa voix, son modèle
Claude) et le branche sur son propre abonnement Claude, sans toucher au code.

Deux façons de l'ouvrir :
- avant le premier lancement : `.venv/bin/python -m jervis.reglages` (installer.command le propose) ;
- quand il tourne : menu de la barre → Réglages…, ou le lien « Réglages » du tableau de bord.

Les choix vont dans reglages.json, à côté du code (jamais dans le dépôt : il est dans .gitignore). Une
variable JERVIS_… l'emporte toujours sur la page. Ils comptent au prochain lancement.
La page n'écoute que sur ce Mac. La connexion à Claude se fait dans le Terminal (`claude auth login`) :
ton mot de passe ne passe ni par cette page, ni par lui.
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, langues

PAGE = Path(__file__).with_name("reglages.html")
MODELES = ("sonnet", "opus", "haiku")
MICROSOFT = re.compile(r"[a-z]{2,3}-[A-Z]{2,4}(?:-[A-Za-z]+)?-\w+Neural")


@dataclass(frozen=True)
class Champ:
    nom: str      # la clé de reglages.json (et la variable JERVIS_<nom>)
    titre: str
    aide: str


CHAMPS = (
    Champ("PRENOM", "Son nom", "Le mot qui le réveille (« Steve, … »). Un nom court, facile à dire."),
    Champ("LANGUE", "Ta langue principale", "Celle de ses consignes, de sa voix par défaut et de ses « Voyons… »."),
    Champ("LANGUES", "Les langues que tu lui parles",
          "Il reconnaît laquelle à chaque phrase et répond dans la même, avec la voix de cette langue. "
          "Une seule : ses filtres anti-vidéo restent les plus stricts."),
    Champ("VOIX_LOCALE", "Sa voix sur le Mac",
          "Pocket TTS : sans Internet, en français, anglais, allemand, espagnol, italien, portugais ou néerlandais. "
          "« non » : la voix Microsoft."),
    Champ("VOIX_NEURONALE", "Sa voix Microsoft (ta langue principale)",
          "Quand la voix du Mac n'a pas ta langue. Seul le texte de ses réponses part chez Microsoft, jamais ta voix. "
          "« non » : 100 % local avec la voix macOS."),
    Champ("MODELE", "Son modèle Claude", "sonnet : rapide ; opus : le plus fort ; haiku : le plus léger."),
)
NOMS = {c.nom for c in CHAMPS}


def fichier() -> Path | None:
    """Où vont tes choix ; None quand JERVIS_REGLAGES=non (les tests, ou si tu veux tout régler par variables)."""
    return None if config.SANS_REGLAGES else config.FICHIER_REGLAGES


def lire() -> dict[str, str]:
    """Le contenu actuel de reglages.json (relu à chaque fois : la page peut l'avoir changé depuis le lancement)."""
    if (chemin := fichier()) is None:
        return {}
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in donnees.items() if isinstance(v, (str, int, float))} \
        if isinstance(donnees, dict) else {}


def _voix_locales() -> list[str]:
    try:
        from .choisir_voix import LOCALES  # import tardif : il charge la voix
        return [nom for nom, _ in LOCALES]
    except Exception:
        return ["giovanni", "estelle", "javert", "juergen"]


def valider(donnees: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Ce que la page envoie → les valeurs propres à écrire (« » = revenir au défaut), et les erreurs en clair."""
    propres, erreurs = {}, []
    for nom in NOMS & set(donnees):
        valeur = donnees[nom]
        if isinstance(valeur, list):
            valeur = ",".join(str(v) for v in valeur)
        if not isinstance(valeur, str):
            erreurs.append(f"{nom} : valeur illisible.")
            continue
        valeur = valeur.strip()
        if not valeur:
            propres[nom] = ""
        elif nom == "PRENOM":
            if not re.fullmatch(r"[^\W\d_][\w' -]{0,23}", valeur):
                erreurs.append("Son nom : des lettres seulement (24 au plus).")
            else:
                propres[nom] = valeur
        elif nom == "LANGUE":
            if valeur not in langues.LANGUES:
                erreurs.append(f"Langue principale inconnue : {valeur}.")
            else:
                propres[nom] = valeur
        elif nom == "LANGUES":
            codes = [c.strip() for c in valeur.split(",") if c.strip()]
            if codes == ["toutes"]:
                propres[nom] = "toutes"
            elif inconnues := [c for c in codes if c not in langues.LANGUES]:
                erreurs.append(f"Langues inconnues : {', '.join(inconnues)}.")
            else:
                propres[nom] = ",".join(dict.fromkeys(codes))
        elif nom == "VOIX_LOCALE":
            if valeur.lower() not in {"non", *_voix_locales()}:
                erreurs.append(f"Voix du Mac inconnue : {valeur}.")
            else:
                propres[nom] = valeur.lower()
        elif nom == "VOIX_NEURONALE":
            if valeur.lower() != "non" and not MICROSOFT.fullmatch(valeur):
                erreurs.append("Voix Microsoft : un nom comme fr-FR-RemyMultilingualNeural, ou « non ».")
            else:
                propres[nom] = valeur if valeur.lower() != "non" else "non"
        elif nom == "MODELE":
            if valeur not in MODELES:
                erreurs.append(f"Modèle inconnu : {valeur} ({', '.join(MODELES)}).")
            else:
                propres[nom] = valeur
    if (principale := propres.get("LANGUE")) and propres.get("LANGUES") not in (None, "", "toutes"):
        if principale not in propres["LANGUES"].split(","):
            propres["LANGUES"] = f"{principale},{propres['LANGUES']}"  # la principale en fait toujours partie
    return propres, erreurs


def ecrire(propres: dict[str, str]) -> Path:
    """Fusionne ces choix dans reglages.json (les autres clés, ajoutées à la main, restent) et l'écrit d'un coup :
    un fichier à moitié écrit ne peut pas casser son prochain démarrage. « » efface la clé (retour au défaut)."""
    if (chemin := fichier()) is None:
        raise PermissionError("JERVIS_REGLAGES=non : la page ne peut rien écrire.")
    donnees = lire()
    for nom, valeur in propres.items():
        if valeur:
            donnees[nom] = valeur
        else:
            donnees.pop(nom, None)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=".reglages-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(temporaire, 0o600)
        os.replace(temporaire, chemin)
    except BaseException:
        Path(temporaire).unlink(missing_ok=True)
        raise
    return chemin


# --- Claude : ton abonnement à toi ------------------------------------------------------------------

def _claude() -> str | None:
    return shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude")) if p.exists()), None)


def lire_statut(sortie: str) -> dict[str, Any]:
    """`claude auth status --json` → ce que la page montre. Ni ton adresse, ni ton organisation : pas besoin."""
    try:
        d = json.loads(sortie)
    except ValueError:
        return {"installe": True, "connecte": False}
    if not isinstance(d, dict):
        return {"installe": True, "connecte": False}
    return {"installe": True, "connecte": bool(d.get("loggedIn")),
            "methode": {"claude.ai": "abonnement Claude", "console": "clé de la console"}.get(
                str(d.get("authMethod", "")), str(d.get("authMethod", "")) or None),
            "abonnement": str(d["subscriptionType"]) if d.get("subscriptionType") else None}


def statut_claude() -> dict[str, Any]:
    """Est-ce que ce Mac est connecté à un compte Claude ? (Ce que Steve utilise pour réfléchir.)"""
    if (claude := _claude()) is None:
        return {"installe": False, "connecte": False}
    try:
        sortie = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {"installe": True, "connecte": False}
    return lire_statut(sortie)


def connecter_claude() -> bool:
    """Ouvre le Terminal sur `claude auth login` : tu te connectes à TON compte, dans le navigateur, sans que
    ton mot de passe passe par ici. (Pas de Claude Code : le Terminal dit comment l'installer.)"""
    claude = _claude()
    commande = (f"{shlex.quote(claude)} auth login" if claude else
                "echo 'Installe d’abord Claude Code : curl -fsSL https://claude.ai/install.sh | bash'")
    script = f'tell application "Terminal"\nactivate\ndo script "{commande}"\nend tell'
    try:
        subprocess.Popen(["osascript", "-e", script])
        return True
    except OSError:
        return False


# --- La page ----------------------------------------------------------------------------------------

def etat() -> dict[str, Any]:
    """Tout ce que la page affiche : tes choix, ce qui tourne en ce moment, et les listes où choisir."""
    choisis = lire()
    return {
        "fichier": str(fichier()) if fichier() else None,
        "choisis": {c.nom: choisis.get(c.nom, "") for c in CHAMPS},
        "en_service": {"PRENOM": config.PRENOM, "LANGUE": config.LANGUE, "LANGUES": config.LANGUES,
                       "VOIX_LOCALE": config.VOIX_LOCALE, "VOIX_NEURONALE": config.VOIX_NEURONALE,
                       "MODELE": config.MODELE_CLAUDE},
        "variables": sorted(c.nom for c in CHAMPS if os.environ.get(f"JERVIS_{c.nom}") is not None),
        "champs": [{"nom": c.nom, "titre": c.titre, "aide": c.aide} for c in CHAMPS],
        "langues": [{"code": l.code, "nom": l.nom, "natif": l.natif, "voix": l.neuronale,
                     "voix_mac": langues.voix_mac(l.code) or "", "parakeet": l.code in langues.PARAKEET,
                     "locale": bool(langues.modele_local(l.code))} for l in langues.LANGUES.values()],
        "voix_locales": _voix_locales(),
        "modeles": list(MODELES),
    }


def page(jeton: str) -> tuple[str, str]:
    """La page et son `nonce` (seul le script qui le porte a le droit de tourner)."""
    nonce = secrets.token_urlsafe(16)
    return (PAGE.read_text(encoding="utf-8").replace("__NONCE__", nonce).replace("__JETON__", jeton)
            .replace("__PRENOM__", html.escape(config.PRENOM, quote=True))), nonce


def regles(nonce: str) -> str:
    return (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def traiter(methode: str, chemin: str, corps: bytes = b"") -> tuple[int, bytes, str]:
    """Les routes /reglages/… (le jeton est vérifié avant, par le serveur qui appelle)."""
    if methode == "GET" and chemin == "/reglages/etat":
        donnees = etat()
        donnees["claude"] = statut_claude()
        return 200, json.dumps(donnees, ensure_ascii=False).encode(), "application/json"
    if methode == "POST" and chemin == "/reglages":
        try:
            recu = json.loads(corps.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            recu = None
        if not isinstance(recu, dict):
            return 400, "Réglages illisibles.".encode(), "text/plain"
        propres, erreurs = valider(recu)
        if erreurs:
            return 422, json.dumps({"erreurs": erreurs}, ensure_ascii=False).encode(), "application/json"
        try:
            ou = ecrire(propres)
        except OSError as e:
            return 500, f"Impossible d'écrire les réglages : {e}".encode(), "text/plain"
        return 200, json.dumps({"fichier": str(ou)}, ensure_ascii=False).encode(), "application/json"
    if methode == "POST" and chemin == "/reglages/claude":
        return (204 if connecter_claude() else 500), b"", "text/plain"
    return 404, b"", "text/plain"


def servir(port: int = 8766, ouvrir_page: bool = True) -> None:
    """La page seule, sans lui : pour régler ton assistant avant son premier lancement."""
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    jeton = secrets.token_urlsafe(24)
    hotes = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Requete(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _repondre(self, code: int, corps: bytes = b"", genre: str = "text/plain", en_plus: dict = {}) -> None:
            self.send_response(code)
            for nom, valeur in {"Content-Type": f"{genre}; charset=utf-8", "Content-Length": str(len(corps)),
                                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **en_plus}.items():
                self.send_header(nom, valeur)
            self.end_headers()
            self.wfile.write(corps)

        def _jeton_ok(self) -> bool:  # un autre site ne connaît pas le jeton, et ne peut pas se faire passer pour nous
            return (self.headers.get("Host", "") in hotes
                    and hmac.compare_digest(self.headers.get("X-Jeton", ""), jeton))

        def do_GET(self) -> None:
            chemin = self.path.partition("?")[0]
            if self.headers.get("Host", "") not in hotes:
                self._repondre(403)
            elif chemin in {"/", "/reglages"}:
                texte, nonce = page(jeton)
                self._repondre(200, texte.encode(), "text/html", {"Content-Security-Policy": regles(nonce)})
            elif not self._jeton_ok():
                self._repondre(403)
            else:
                self._repondre(*traiter("GET", chemin))

        def do_POST(self) -> None:
            try:
                taille = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                taille = -1
            if not 0 <= taille <= 20_000:
                self.close_connection = True
                self._repondre(413, en_plus={"Connection": "close"})
                return
            corps = self.rfile.read(taille)
            if not self._jeton_ok():
                self._repondre(403)
            else:
                self._repondre(*traiter("POST", self.path.partition("?")[0], corps))

    serveur = ThreadingHTTPServer(("127.0.0.1", port), Requete)
    adresse = f"http://127.0.0.1:{port}/"
    print(f"Réglages : {adresse}  (ctrl-C pour fermer ; tes choix comptent au prochain lancement)", flush=True)
    if ouvrir_page:
        subprocess.Popen(["open", adresse])
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="La page Réglages de ton assistant (nom, langues, voix, Claude).")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--sans-ouvrir", action="store_true", help="ne pas ouvrir le navigateur")
    a = p.parse_args()
    servir(a.port, not a.sans_ouvrir)
