"""Ce que coûte Steve. Chaque réponse de Claude est notée dans espace/depenses.jsonl.

Sur l'abonnement Claude, tu ne paies pas à la réponse. Le montant en dollars est ce que la même chose coûterait
avec une clé API : c'est la bonne mesure pour comparer (Sonnet ou Haiku, tâches de fond…). Ce qui te limite pour
de vrai, c'est la fenêtre de 5 heures et le quota de la semaine : Claude Code en donne le pourcentage utilisé
(gardé dans espace/limites.json).
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from . import config

DEPENSES = config.ESPACE / "depenses.jsonl"
LIMITES = config.ESPACE / "limites.json"

NOMS_LIMITES = {"five_hour": "la fenêtre de 5 heures", "seven_day": "la semaine",
                "seven_day_opus": "la semaine (Opus)", "seven_day_sonnet": "la semaine (Sonnet)",
                "overage": "l'usage en plus (payant)"}


class Totaux:
    """Claude Code donne des totaux qui grossissent toute la session (coût, jetons) : on en tire ce que
    vient de coûter la dernière réponse. Un total qui redescend = conversation remise à zéro (/clear)."""

    def __init__(self) -> None:
        self._avant: dict[str, float] = {}

    def ecart(self, maintenant: dict[str, float]) -> dict[str, float]:
        if any(v < self._avant.get(k, 0) for k, v in maintenant.items()):
            self._avant = {}
        ecart = {k: v - self._avant.get(k, 0) for k, v in maintenant.items()}
        self._avant = dict(maintenant)
        return ecart


def chiffres(message: Any) -> dict[str, float]:
    """Les totaux d'un ResultMessage : coût (équivalent API) et jetons, tous modèles confondus."""
    total = {"cout": float(message.total_cost_usd or 0), "entree": 0.0, "cache": 0.0, "sortie": 0.0}
    for usage in (message.model_usage or {}).values():
        total["entree"] += usage.get("inputTokens", 0) + usage.get("cacheCreationInputTokens", 0)
        total["cache"] += usage.get("cacheReadInputTokens", 0)
        total["sortie"] += usage.get("outputTokens", 0)
    return total


def noter(source: str, ecart: dict[str, float]) -> None:
    """source = steve, tache ou memoire."""
    if ecart.get("cout", 0) <= 0:
        return
    ligne = {"t": round(time.time()), "source": source, **{k: round(v, 5) for k, v in ecart.items()}}
    try:
        config.ESPACE.mkdir(exist_ok=True)
        with DEPENSES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne) + "\n")
    except OSError:
        pass


def _lignes(depuis: float) -> list[dict]:
    try:
        texte = DEPENSES.read_text(encoding="utf-8")
    except OSError:
        return []
    lignes = []
    for brut in texte.splitlines():
        try:
            ligne = json.loads(brut)
        except ValueError:
            continue
        if ligne.get("t", 0) >= depuis:
            lignes.append(ligne)
    return lignes


def _minuit(jours_avant: int = 0) -> float:
    aujourdhui = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (aujourdhui - dt.timedelta(days=jours_avant)).timestamp()


def aujourdhui() -> float:
    return sum(l.get("cout", 0) for l in _lignes(_minuit()))


def par_jour(n: int = 7) -> list[dict[str, Any]]:
    """Les `n` derniers jours, du plus ancien à aujourd'hui :
    [{"jour": "2026-11-23", "cout": 2.49, "jetons": 1200000, "appels": 23}, …].
    Les jetons = tout ce que Claude a lu et écrit ce jour-là (relectures du cache comprises)."""
    totaux = {(dt.date.today() - dt.timedelta(days=i)).isoformat(): {"cout": 0.0, "jetons": 0.0, "appels": 0}
              for i in range(n - 1, -1, -1)}
    for ligne in _lignes(_minuit(n - 1)):
        if total := totaux.get(dt.date.fromtimestamp(ligne["t"]).isoformat()):
            total["cout"] += ligne.get("cout", 0)
            total["jetons"] += ligne.get("entree", 0) + ligne.get("cache", 0) + ligne.get("sortie", 0)
            total["appels"] += 1
    return [{"jour": jour, "cout": round(t["cout"], 4), "jetons": round(t["jetons"]), "appels": t["appels"]}
            for jour, t in totaux.items()]


SEUILS = (0.8, 0.95)  # il te prévient quand une limite passe 80 %, puis 95 %


def noter_limite(info: Any) -> list[str]:
    """Garde le dernier état de tes limites d'abonnement. Renvoie les alertes à te dire (souvent aucune).
    Le détail est dans info.raw["unifiedWindows"] : {"five_hour": {"utilization": 0.32, "resetsAt": …}, …}."""
    try:
        avant = json.loads(LIMITES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        avant = {}
    raw = info.raw or {}
    fenetres = {g: {"utilization": f.get("utilization"), "resets_at": f.get("resetsAt"), "status": f.get("status")}
                for g, f in (raw.get("unifiedWindows") or {}).items() if isinstance(f, dict)}
    if not fenetres and info.rate_limit_type:
        fenetres[info.rate_limit_type] = {"utilization": info.utilization, "resets_at": info.resets_at,
                                          "status": info.status}
    etat = {"fenetres": {**avant.get("fenetres", {}), **fenetres}, "vu": round(time.time()),
            "supplement": {"status": info.overage_status, "raison": info.overage_disabled_reason,
                           "en_cours": bool(raw.get("isUsingOverage"))}}
    try:
        LIMITES.write_text(json.dumps(etat, indent=1), encoding="utf-8")
    except OSError:
        pass
    alertes = []
    for genre, f in fenetres.items():
        ancien = avant.get("fenetres", {}).get(genre, {})
        u, u_avant = f.get("utilization") or 0, ancien.get("utilization") or 0
        if (f.get("resets_at") or 0) > (ancien.get("resets_at") or 0) + 60:
            u_avant = 0  # nouvelle fenêtre : les seuils repartent de zéro
        if f.get("status") == "rejected" and ancien.get("status") != "rejected":
            alertes.append(phrase_limite(genre, f))
        elif any(u_avant < seuil <= u for seuil in SEUILS):
            alertes.append(phrase_limite(genre, f))
    if raw.get("isUsingOverage") and not avant.get("supplement", {}).get("en_cours"):
        alertes.append("Attention : tu as dépassé ton abonnement, là c'est de l'usage en plus, payant.")
    return alertes


def _heure(horodatage: float | None) -> str:
    if not horodatage:
        return ""
    quand = dt.datetime.fromtimestamp(horodatage)
    jour = "" if quand.date() == dt.date.today() else f"le {quand.day} "
    return f"{jour}à {quand.strftime('%H h %M')}"


def phrase_limite(genre: str, f: dict) -> str:
    nom = NOMS_LIMITES.get(genre, "ta limite")
    reprise = f", ça se libère {_heure(f.get('resets_at'))}" if f.get("resets_at") else ""
    if f.get("status") == "rejected":
        return f"Attention, tu as atteint la limite de ton abonnement pour {nom}{reprise}."
    return f"Au fait, tu as utilisé {(f.get('utilization') or 0) * 100:.0f} % de {nom}{reprise}."


def dollars(montant: float) -> str:
    return f"{montant:.2f} $".replace(".", ",") if montant >= 0.1 else f"{montant * 100:.1f} cents".replace(".", ",")


def bilan() -> str:
    """Le point sur les dépenses, pour qu'il te le dise à voix haute."""
    morceaux = []
    for nom, depuis in (("Aujourd'hui", _minuit()), ("Ces 7 derniers jours", _minuit(6)),
                        ("Ces 30 derniers jours", _minuit(29))):
        lignes = _lignes(depuis)
        if not lignes:
            morceaux.append(f"{nom} : rien.")
            continue
        par_source: dict[str, float] = {}
        for l in lignes:
            par_source[l.get("source", "steve")] = par_source.get(l.get("source", "steve"), 0) + l.get("cout", 0)
        detail = ", ".join(f"{s} {dollars(c)}" for s, c in sorted(par_source.items(), key=lambda x: -x[1]))
        morceaux.append(f"{nom} : {dollars(sum(par_source.values()))} pour {len(lignes)} réponses ({detail}).")
    try:
        limites = json.loads(LIMITES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        limites = {}
    for genre, f in limites.get("fenetres", {}).items():
        if f.get("resets_at") and f["resets_at"] < time.time():
            morceaux.append(f"{NOMS_LIMITES.get(genre, genre).capitalize()} : repartie à zéro depuis.")
        elif f.get("utilization") is not None:
            morceaux.append(f"{NOMS_LIMITES.get(genre, genre).capitalize()} : {f['utilization'] * 100:.0f} % "
                            f"utilisés, ça se libère {_heure(f.get('resets_at'))}.")
    supplement = limites.get("supplement", {})
    if supplement.get("en_cours"):
        morceaux.append("Tu es en usage en plus, payant : là, ça coûte de l'argent pour de vrai.")
    elif supplement.get("status") == "rejected":
        morceaux.append("L'usage en plus payant est coupé : tu ne peux pas payer plus que ton abonnement.")
    if not limites:
        morceaux.append("Pourcentage de l'abonnement : pas encore reçu (il arrive avec la première réponse). "
                        "Il est aussi sur claude.ai, Paramètres, Utilisation.")
    morceaux.append("Ces montants sont l'équivalent en prix API : sur ton abonnement tu ne les paies pas, "
                    "ils comptent dans tes limites de 5 heures et de la semaine.")
    return "\n".join(morceaux)
