"""Port LLM + implémentations (Anthropic réel / Mock pour tests).

Deux "tiers" seulement :
- small : extraction de signaux, désambiguïsation (Haiku-class)
- main  : décision de coaching + rédaction (Sonnet-class), 1 appel / échange

Chaque appel remonte son usage (tokens, cache) -> télémétrie FinOps par nœud.

v0.40 : `tool_loop(...)` = boucle d'outils (tool use Anthropic) pour les spécialistes de l'équipe : le modèle appelle
des outils Python, on exécute, on renvoie `tool_result`, jusqu'à la réponse finale (outil forcé au dernier tour),
au plus `max_steps` allers-retours. Chaque aller-retour est journalisé sous le nom de l'agent (coût par agent).
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
Tier = Literal["small", "main"]

# Identifiants vérifiés (docs Anthropic, sept. 2026).
MODELS: dict[Tier, str] = {
    "small": os.getenv("COACH_MODEL_SMALL", "claude-haiku-4-5"),
    "main": os.getenv("COACH_MODEL_MAIN", "claude-sonnet-5"),
}
# Sonnet 5.5 (même prix que Sonnet 5) refuse tool_choice forcé et la désactivation
# de la réflexion : migration à faire (tools strict + tool_choice auto) avant usage.
_FORCED_TOOL_UNSUPPORTED = ("claude-sonnet-5-5",)


@dataclass
class Usage:
    node: str
    tier: Tier
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class AgentResult:
    """Sortie d'un agent outillé : objet final validé + trace des outils appelés `{name, ms}`."""
    output: BaseModel
    tool_calls: list[dict] = field(default_factory=list)
    steps: int = 1


FINAL_TOOL = "reponse_finale"
MAX_AGENT_STEPS = 4
# v0.48 : 1024 jetons ne suffisaient pas toujours à la décision du coach (liste de changements + message) : l'entrée
# de l'outil arrivait tronquée (stop_reason « max_tokens »), la validation échouait et l'application affichait
# « Claude est indisponible ». Plafonds relevés, et une relance avec le double si la sortie est quand même coupée.
MAX_TOKENS: dict[Tier, int] = {"small": int(os.getenv("COACH_MAX_TOKENS_SMALL", "1500")),
                               "main": int(os.getenv("COACH_MAX_TOKENS_MAIN", "4096"))}
AGENT_MAX_TOKENS = int(os.getenv("COACH_MAX_TOKENS_AGENT", "2048"))
MAX_TOKENS_CEILING = 8192


class TruncatedOutput(RuntimeError):
    """La sortie de Claude a été coupée (max_tokens) avant la fin de l'objet demandé."""


class NoToolUse(RuntimeError):
    """Claude a répondu sans appeler l'outil demandé (refus, texte libre)."""


def tool_schema(schema: type[BaseModel]) -> dict:
    """Schéma JSON d'un modèle pydantic pour `input_schema`, références `$defs` remplacées par leur définition
    (schéma autonome : pas de `$ref` à résoudre côté API, quelle que soit la version)."""
    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(x, depth=0):
        if depth > 20:
            return {}
        if isinstance(x, dict):
            ref = x.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                rest = {k: v for k, v in x.items() if k != "$ref"}
                return inline({**target, **rest}, depth + 1)
            return {k: inline(v, depth + 1) for k, v in x.items()}
        if isinstance(x, list):
            return [inline(v, depth + 1) for v in x]
        return x
    out = inline(raw)
    out.setdefault("type", "object")
    return out


class LLMPort(Protocol):
    usage_log: list[Usage]

    def structured(self, *, node: str, tier: Tier, system: str, user: str,
                   schema: type[T]) -> T: ...

    # v0.40 (facultatif : sans elle, l'équipe passe par l'implémentation hors ligne)
    def tool_loop(self, *, node: str, tier: Tier, system: str, user: str, tools, schema: type[T],
              max_steps: int = MAX_AGENT_STEPS) -> AgentResult: ...


class AnthropicLLM:
    """Sortie structurée via tool use forcé + cache du prompt système."""

    def __init__(self, client=None):
        import anthropic  # import paresseux : les tests n'en dépendent pas
        # v0.25 : 3 nouvelles tentatives espacées (429 limite, 529 surcharge) et 60 s maximum par appel
        self.client = client or anthropic.Anthropic(max_retries=3, timeout=60.0)
        self.usage_log: list[Usage] = []

    def structured(self, *, node, tier, system, user, schema):
        if MODELS[tier].startswith(_FORCED_TOOL_UNSUPPORTED):
            raise NotImplementedError(
                f"{MODELS[tier]} : tool_choice forcé non supporté, migration requise (voir llm.py).")
        tool = {
            "name": schema.__name__,
            "description": f"Retourne un objet {schema.__name__} valide.",
            "input_schema": tool_schema(schema),
        }
        max_tokens = MAX_TOKENS[tier]
        while True:
            resp = self.client.messages.create(
                model=MODELS[tier],
                max_tokens=max_tokens,
                # Le prompt système (RACO + règles) est identique pour TOUS les
                # athlètes -> préfixe partagé, cache chaud dès qu'il y a du trafic.
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": user}],
            )
            self._log(node, tier, resp)
            if getattr(resp, "stop_reason", None) == "max_tokens" and max_tokens < MAX_TOKENS_CEILING:
                max_tokens = min(MAX_TOKENS_CEILING, max_tokens * 2)    # sortie coupée : une relance, plus large
                continue
            break
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise TruncatedOutput(f"{node} : réponse coupée à {max_tokens} jetons ({MODELS[tier]})")
        if block is None:
            raise NoToolUse(f"{node} : pas d'appel à l'outil {tool['name']} (stop_reason={getattr(resp, 'stop_reason', '?')})")
        return schema.model_validate(block.input)

    def _log(self, node: str, tier: Tier, resp) -> None:
        u = resp.usage
        self.usage_log.append(Usage(
            node=node, tier=tier, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0))

    def tool_loop(self, *, node, tier, system, user, tools, schema, max_steps=MAX_AGENT_STEPS):
        """Boucle d'outils. `tools` : ToolBox (coach/agents/tools.py) qui exécute et chronomètre chaque appel.

        tool_choice « any » : le modèle DOIT appeler un outil (un outil de données ou la réponse finale) ; au
        dernier aller-retour, la réponse finale est forcée. Les modèles de MODELS l'acceptent (pas Sonnet 5.5).
        Cache : un seul point de cache sur le DERNIER outil (préfixe outils) + le prompt système."""
        if MODELS[tier].startswith(_FORCED_TOOL_UNSUPPORTED):
            raise NotImplementedError(
                f"{MODELS[tier]} : tool_choice forcé non supporté, migration requise (voir llm.py).")
        final = {"name": FINAL_TOOL, "description": f"Réponse finale de l'agent : un objet {schema.__name__} "
                 "valide. À appeler une fois les données lues.", "input_schema": tool_schema(schema)}
        defs = [{k: v for k, v in t.spec().items() if k != "cache_control"} for t in tools.tools] + [final]
        defs[-1] = {**defs[-1], "cache_control": {"type": "ephemeral"}}   # prompt + outils : préfixe en cache
        messages: list[dict] = [{"role": "user", "content": user}]
        for step in range(max_steps):
            last = step == max_steps - 1
            resp = self.client.messages.create(
                model=MODELS[tier], max_tokens=AGENT_MAX_TOKENS,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=defs, tool_choice={"type": "tool", "name": FINAL_TOOL} if last else {"type": "any"},
                messages=messages)
            self._log(node, tier, resp)
            uses = [b for b in resp.content if b.type == "tool_use"]
            fin = next((b for b in uses if b.name == FINAL_TOOL), None)
            if getattr(resp, "stop_reason", None) == "max_tokens":
                raise TruncatedOutput(f"{node} : réponse coupée à {AGENT_MAX_TOKENS} jetons ({MODELS[tier]})")
            if fin is not None:
                return AgentResult(schema.model_validate(fin.input), list(tools.calls), steps=step + 1)
            if not uses:                                    # ne devrait pas arriver avec tool_choice any
                break
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": b.text} if b.type == "text" else
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                for b in resp.content if b.type in ("text", "tool_use")]})
            results = []
            for b in uses:
                out = tools.call(b.name, b.input if isinstance(b.input, dict) else {})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)[:6000]})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"{node} : pas de réponse finale après {max_steps} allers-retours")


class FallbackLLM:
    """v0.25 : Claude d'abord ; s'il est indisponible (limite atteinte, surcharge, crédit épuisé, réseau), la
    réponse vient du mode hors ligne (règles) au lieu d'un message d'erreur. `degraded` = dernier repli."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.client = getattr(primary, "client", None)          # recherche web de courses (race_lookup)
        self.usage_log = primary.usage_log
        self.degraded: str | None = None
        self.degraded_detail: str | None = None    # v0.48 : détail court (type d'erreur, modèle), sans secret
        self.failures = 0
        self.agent_failures = 0          # v0.40 : spécialistes passés hors ligne (la réponse du coach reste Claude)

    def structured(self, *, node, tier, system, user, schema):
        try:
            out = self.primary.structured(node=node, tier=tier, system=system, user=user, schema=schema)
            self.degraded = None
            return out
        except NotImplementedError:
            raise
        except Exception as e:                                   # noqa: BLE001
            import logging
            self.failures += 1
            self.degraded, self.degraded_detail = _reason(e, MODELS[tier]), _detail(e, MODELS[tier], node)
            logging.getLogger("coach.llm").warning("Claude indisponible pour le nœud %s (%s, %s) : %s : %s — mode "
                                                   "hors ligne", node, tier, MODELS[tier], type(e).__name__,
                                                   _sanitize(str(e), 2000))
            if hasattr(self.fallback, "today"):
                from datetime import date
                self.fallback.today = date.today()
            return self.fallback.structured(node=node, tier=tier, system=system, user=user, schema=schema)

    def tool_loop(self, *, node, tier, system, user, tools, schema, max_steps=MAX_AGENT_STEPS):
        """v0.40 : repli agent par agent (un spécialiste en échec passe en hors ligne, les autres restent Claude)."""
        try:
            out = self.primary.tool_loop(node=node, tier=tier, system=system, user=user, tools=tools, schema=schema,
                                     max_steps=max_steps)
            return out
        except NotImplementedError:
            raise
        except Exception as e:                                   # noqa: BLE001
            import logging
            self.agent_failures += 1
            logging.getLogger("coach.llm").warning("Claude indisponible pour l'agent %s (%s, %s) : %s : %s — agent "
                                                   "hors ligne", node, tier, MODELS[tier], type(e).__name__,
                                                   _sanitize(str(e), 2000))
            tools.calls.append({"name": "repli_hors_ligne", "ms": 0.0})
            tools.degraded = _reason(e, MODELS[tier])
            return self.fallback.tool_loop(node=node, tier=tier, system=system, user=user, tools=tools, schema=schema,
                                       max_steps=max_steps)


_SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{20,}|"
                     r"(?i:(?:x-api-key|authorization|api[_-]?key)[\"':= ]+)[^\s,\"'}]+")


def _sanitize(t: str, n: int = 160) -> str:
    """Message d'erreur sans clé ni jeton, sur une ligne, tronqué."""
    t = _SECRET.sub("…", t or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _status(e: Exception) -> int | None:
    st = getattr(e, "status_code", None)
    if isinstance(st, int):
        return st
    resp = getattr(e, "response", None)
    st = getattr(resp, "status_code", None)
    return st if isinstance(st, int) else None


def _api_message(e: Exception) -> str:
    """Message de l'API (corps JSON `error.message`) si l'exception le porte, sinon le texte de l'exception."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return getattr(e, "message", None) or str(e)


def _reason(e: Exception, model: str | None = None) -> str:
    """Raison lisible par l'athlète (affichée dans « Réponse simplifiée : … »)."""
    name = type(e).__name__
    st = _status(e)
    t = f"{name} {_api_message(e)}".lower()
    if "credit" in t or "billing" in t:
        return "crédit de l'API Claude épuisé"
    if st == 401 or name == "AuthenticationError" or "api_key" in t or "api key" in t and "invalid" in t:
        return "clé API Claude absente ou invalide"
    if st == 403 or name == "PermissionDeniedError":
        return "accès refusé par l'API Claude (droits de la clé)"
    if st == 404 or name == "NotFoundError":
        return f"modèle Claude inconnu ({model})" if model else "modèle Claude inconnu"
    if st == 429 or "ratelimit" in t or "rate limit" in t or "rate_limit" in t:
        return "limite d'utilisation de l'API Claude atteinte"
    if st == 529 or "overload" in t:
        return "Claude est surchargé"
    if st == 400 or name == "BadRequestError":
        return "requête refusée par Claude : " + _sanitize(_api_message(e), 120)
    if name == "TruncatedOutput":
        return "réponse de Claude trop longue (coupée)"
    if name in ("ValidationError", "NoToolUse"):
        return "réponse de Claude incomplète"
    if "budget" in t:
        return "budget Claude du jour atteint"
    if "timeout" in t or "connection" in t or name in ("APIConnectionError", "APITimeoutError", "ConnectError"):
        return "Claude ne répond pas (réseau)"
    if st and st >= 500:
        return f"erreur du service Claude ({st})"
    return f"Claude est indisponible ({name})"


def _detail(e: Exception, model: str | None, node: str) -> str:
    """Détail court pour « détails » du message : nœud, modèle, type et code d'erreur, message nettoyé."""
    st = _status(e)
    return _sanitize(f"{node} · {model or '?'} · {type(e).__name__}" + (f" {st}" if st else "")
                     + f" : {_api_message(e)}", 240)


@dataclass
class MockLLM:
    """LLM scripté : une fonction par nœud -> tests déterministes, 0 €.

    v0.40 : les nœuds de l'équipe d'agents NON scriptés (superviseur, spécialistes, critique) passent par
    l'équipe hors ligne déterministe ; ils sont listés dans `team_calls`, pas dans `calls` (nœuds scriptés)."""
    handlers: dict[str, Callable[[str], BaseModel]]
    usage_log: list[Usage] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    team_calls: list[str] = field(default_factory=list)

    def structured(self, *, node, tier, system, user, schema):
        self.usage_log.append(Usage(node=node, tier=tier,
                                    input_tokens=len(system + user) // 4, output_tokens=150))
        if node not in self.handlers:
            from .agents.offline import TEAM_NODES, offline_structured
            if node in TEAM_NODES:
                self.team_calls.append(node)
                return offline_structured(node, user, schema)
        self.calls.append(node)
        out = self.handlers[node](user)
        return schema.model_validate(out.model_dump())

    def tool_loop(self, *, node, tier, system, user, tools, schema, max_steps=MAX_AGENT_STEPS):
        self.usage_log.append(Usage(node=node, tier=tier, input_tokens=len(system + user) // 4, output_tokens=150))
        if node in self.handlers:
            self.calls.append(node)
            return AgentResult(schema.model_validate(self.handlers[node](user).model_dump()), [])
        from .agents.offline import offline_agent
        self.team_calls.append(node)
        return offline_agent(node, user, tools, schema)


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return out, round((time.perf_counter() - t0) * 1000, 1)
