"""Le graphe de ce qu'il sait sur toi : les gens, les lieux, les projets, et les liens entre eux, dans le temps.

La mémoire (memoire.md) est une liste de phrases. Le graphe (Graphiti) les relie : « Léa est ta sœur »,
« Léa habite à Lyon », « Oqro, c'est ton projet avec Marc » deviennent des personnes et des liens. Il peut
alors répondre à « où habite ma sœur ? » sans que la phrase existe telle quelle, et il sait ce qui a changé
(« tu travaillais chez X jusqu'en mars, maintenant chez Y ») : un lien devenu faux n'est pas effacé, il est
daté « plus vrai depuis ».

Ce qui entre dans le graphe : seulement les faits que la relecture automatique vient de retenir dans
memoire.md (jamais une conversation brute, jamais un mail ou une page). Tout reste sur le Mac :
  - la base est un fichier (espace/graphe.db, FalkorDB embarqué : pas de serveur à installer) ;
  - les « empreintes » des phrases (embeddings) sont calculées par Ollama, en local ;
  - le travail de lecture (qui est qui, quel lien) passe par Claude, comme la relecture de la mémoire ;
  - la télémétrie de Graphiti est coupée avant même son chargement.
Sans Graphiti ou sans Ollama, rien ne casse : le graphe est simplement absent, memoire.md suffit.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import tool

from . import config

# Avant tout import de graphiti_core : pas de télémétrie, et la taille des empreintes de notre modèle local.
os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
os.environ["EMBEDDING_DIM"] = str(config.GRAPHE_DIM)

ACTIF = config.GRAPHE and all(importlib.util.find_spec(m) is not None for m in ("graphiti_core", "redislite"))
# Les faits disent « il », « sa sœur », « son projet » : sans cette consigne, chaque fait aurait son propre
# « il » anonyme, et « sa sœur » ne serait reliée à personne.
CONSIGNE = ("Ces faits parlent tous de la même personne : l'utilisateur, celui pour qui travaille l'assistant. "
            "« il », « lui », « sa », « son », « ses » et « l'utilisateur » le désignent toujours : une seule "
            "entité, nommée « l'utilisateur ». Relie-lui ses proches, ses lieux, son travail et ses projets. "
            "Écris les faits en français, comme ils sont donnés.")
VU_MAX = 12            # au-delà, ce n'est plus une réponse qu'on lit à voix haute
_JSON = re.compile(r"\{.*\}", re.DOTALL)

_graphe: Any = None
_panne: str | None = None          # pourquoi le graphe ne marche pas (dit une fois, puis on n'insiste plus)
_verrou: asyncio.Lock | None = None


def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def lire_json(texte: str) -> dict[str, Any]:
    """L'objet JSON d'une réponse de Claude (même entouré de ```json ou d'une phrase)."""
    m = _JSON.search(texte or "")
    if not m:
        raise ValueError(f"pas de JSON dans la réponse : {texte[:200]!r}")
    return json.loads(m.group(0))


def cosinus(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def decrire(liens: list[Any]) -> list[str]:
    """Les liens trouvés, en phrases datées : ce qui est vrai d'abord, puis ce qui ne l'est plus."""
    vrais, passes = [], []
    for l in liens:
        depuis = f" (depuis le {l.valid_at:%d/%m/%Y})" if getattr(l, "valid_at", None) else ""
        fin = getattr(l, "invalid_at", None) or getattr(l, "expired_at", None)
        if fin:
            passes.append(f"- plus vrai depuis le {fin:%d/%m/%Y} : {l.fact}")
        else:
            vrais.append(f"- {l.fact}{depuis}")
    return (vrais + passes)[:VU_MAX]


# --- Les trois clients dont Graphiti a besoin, sans clé d'API ni service en ligne -------------------------

def _classes() -> tuple[type, type, type]:
    """Construites à la demande : importer graphiti_core prend une seconde, inutile si le graphe est coupé."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import LLMConfig
    import httpx

    from . import compteur

    class ClaudeLecteur(LLMClient):
        """Graphiti demande « qui est qui, quel lien » : Claude répond (abonnement, comme la mémoire)."""

        def __init__(self) -> None:
            super().__init__(LLMConfig(model=config.MODELE_GRAPHE, small_model=config.MODELE_GRAPHE))

        async def _generate_response(self, messages, response_model=None, max_tokens=0, model_size=None,
                                     *, model=None) -> dict[str, Any]:
            systeme = "\n\n".join(m.content for m in messages if m.role == "system")
            demande = "\n\n".join(m.content for m in messages if m.role != "system")
            options = ClaudeAgentOptions(
                system_prompt=systeme + "\n\nRéponds uniquement par l'objet JSON demandé, sans texte autour.",
                model=config.MODELE_GRAPHE, tools=[], setting_sources=[], max_turns=1, cwd=str(config.ESPACE),
                env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
            erreur = None
            for _essai in range(2):
                texte = ""
                async for message in query(prompt=demande + (f"\n\n(Essai précédent refusé : {erreur})"
                                                             if erreur else ""), options=options):
                    if isinstance(message, AssistantMessage):
                        texte += "".join(b.text for b in message.content if isinstance(b, TextBlock))
                    elif isinstance(message, ResultMessage):
                        compteur.noter("graphe", compteur.chiffres(message))
                        texte = message.result or texte
                try:
                    donnees = lire_json(texte)
                    return response_model.model_validate(donnees).model_dump() if response_model else donnees
                except Exception as e:  # JSON mal formé ou incomplet : il réessaie une fois, en sachant pourquoi
                    erreur = str(e)[:300]
            raise ValueError(f"réponse illisible pour le graphe : {erreur}")

    class EmpreintesOllama(EmbedderClient):
        """Les empreintes des phrases, calculées par Ollama sur le Mac."""

        async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{config.OLLAMA_URL}/api/embed",
                                      json={"model": config.GRAPHE_EMBED, "input": list(input_data_list)})
                r.raise_for_status()
                return [v[:config.GRAPHE_DIM] for v in r.json()["embeddings"]]

        async def create(self, input_data) -> list[float]:
            texte = input_data if isinstance(input_data, str) else " ".join(map(str, input_data))
            return (await self.create_batch([texte]))[0]

    class Classement(CrossEncoderClient):
        """Le classement final des résultats, par ressemblance d'empreintes (pas de second modèle à charger)."""

        def __init__(self, empreintes: EmpreintesOllama) -> None:
            self._empreintes = empreintes

        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            if not passages:
                return []
            vecteurs = await self._empreintes.create_batch([query, *passages])
            notes = [(p, cosinus(vecteurs[0], v)) for p, v in zip(passages, vecteurs[1:])]
            return sorted(notes, key=lambda x: -x[1])

    return ClaudeLecteur, EmpreintesOllama, Classement


async def _sauver() -> None:
    """La base embarquée vit en mémoire : on l'écrit sur le disque après chaque changement (il y en a peu)."""
    await _graphe.driver.client.client.save()


async def _ouvrir() -> Any:
    """Le graphe, ouvert une seule fois (la base embarquée démarre au premier usage)."""
    global _graphe
    if _graphe is None:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from redislite.async_falkordb_client import AsyncFalkorDB

        ClaudeLecteur, EmpreintesOllama, Classement = _classes()
        config.FICHIER_GRAPHE.parent.mkdir(parents=True, exist_ok=True)
        base = AsyncFalkorDB(str(config.FICHIER_GRAPHE))
        pilote = FalkorDriver(falkor_db=base, database="steve")
        empreintes = EmpreintesOllama()
        graphe = Graphiti(graph_driver=pilote, llm_client=ClaudeLecteur(), embedder=empreintes,
                          cross_encoder=Classement(empreintes), max_coroutines=config.GRAPHE_PARALLELE)
        await graphe.build_indices_and_constraints()
        _graphe = graphe
    return _graphe


def _expliquer(e: BaseException) -> str:
    """Ce qui coince, en clair. Ollama éteint met le graphe en pause jusqu'à la relance de Steve (inutile
    de réessayer à chaque conversation) ; une autre erreur ne vaut que pour cette fois."""
    global _panne
    if "connect" not in repr(e).lower():
        return repr(e)[:200]
    _panne = (f"Ollama ne répond pas sur {config.OLLAMA_URL} : lance « ollama serve », puis « ollama pull "
              f"{config.GRAPHE_EMBED} »")
    return _panne


def _existe() -> bool:
    """Il y a un graphe à toucher : déjà ouvert pendant cette session, ou écrit sur le disque avant."""
    return _graphe is not None or config.FICHIER_GRAPHE.exists()


def disponible() -> bool:
    return ACTIF and _panne is None


async def ajouter(faits: list[str], quand: datetime | None = None) -> int:
    """Les faits que la mémoire vient de retenir entrent dans le graphe (un seul épisode par conversation).
    Renvoie le nombre de liens créés ou mis à jour. Lève une exception si ça coince (l'appelant la note)."""
    global _verrou
    faits = [" ".join(str(f).split()) for f in faits if str(f).strip()]
    if not (disponible() and faits):
        return 0
    _verrou = _verrou or asyncio.Lock()
    async with _verrou:  # deux relectures en même temps se marcheraient dessus
        from graphiti_core.nodes import EpisodeType
        try:
            graphe = await _ouvrir()
            quand = quand or datetime.now(timezone.utc)
            resultat = await graphe.add_episode(
                name=f"conversation du {quand:%d/%m/%Y %H:%M}", episode_body="\n".join(faits),
                source=EpisodeType.text, reference_time=quand, custom_extraction_instructions=CONSIGNE,
                source_description=f"faits sur la personne pour qui travaille {config.PRENOM}, retenus de vos conversations")
        except Exception as e:
            _expliquer(e)
            raise
            await _sauver()
        return len(resultat.edges)


async def chercher(question: str, n: int = 8) -> list[str]:
    """Les liens qui répondent à la question, en phrases datées."""
    if not disponible():
        return []
    graphe = await _ouvrir()
    return decrire(await graphe.search(question, num_results=max(1, min(n, VU_MAX))))


async def oublier(texte: str) -> int:
    """Efface les liens qui contiennent ce texte (avec l'outil oublier). Renvoie combien."""
    if not (disponible() and texte.strip() and _existe()):
        return 0
    graphe = await _ouvrir()
    lignes, _, _ = await graphe.driver.execute_query(
        "MATCH ()-[e:RELATES_TO]->() WHERE toLower(e.fact) CONTAINS toLower($t) "
        "WITH e, e.uuid AS id DELETE e RETURN count(id) AS n", t=texte.strip())
    n = int(lignes[0]["n"]) if lignes else 0
    if n:
        await _sauver()
    return n


async def effacer() -> bool:
    """Vide le graphe (avec effacer_memoire « memoire » ou « tout »). Vrai s'il y avait quelque chose."""
    if not (ACTIF and _existe()):
        return False
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data
    graphe = await _ouvrir()
    await clear_data(graphe.driver)
    await _sauver()
    return True


@tool("chercher_liens", "Cherche dans le graphe de ce que tu sais sur lui : les gens, les lieux, les projets et "
      "les liens entre eux, avec ce qui a changé dans le temps. Pour une question qui relie deux choses (« où "
      "habite ma sœur ? », « avec qui je bosse sur Oqro ? », « où je travaillais avant ? ») ou quand ta mémoire "
      "ne contient pas la phrase telle quelle. `question` = la question en clair. Un lien « plus vrai depuis » "
      "est du passé : ne le donne pas comme actuel.", {"question": str})
async def chercher_liens(args: dict[str, Any]) -> dict[str, Any]:
    if not ACTIF:
        return _texte("Le graphe est coupé sur ce Mac : réponds avec ta mémoire.")
    if _panne:
        return _texte(f"Le graphe ne marche pas en ce moment ({_panne}) : réponds avec ta mémoire.")
    try:
        lignes = await chercher(str(args.get("question") or ""))
    except Exception as e:
        return _texte(f"Le graphe ne répond pas ({_expliquer(e)}) : réponds avec ta mémoire.")
    return _texte("\n".join(lignes) if lignes else "Rien dans le graphe là-dessus.")
