"""AnthropicLLM testé SANS clé ni réseau : transport HTTP simulé.

Vérifie la requête réellement envoyée (modèle, outil forcé, cache du prompt
système, schéma) et le parsing d'une réponse (bloc texte + bloc tool_use).
"""
import json

import pytest

httpx = pytest.importorskip("httpx2")   # le SDK anthropic 1.x embarque httpx2

anthropic = pytest.importorskip("anthropic")

from coach.llm import AnthropicLLM  # noqa: E402
from coach.schemas import Signals  # noqa: E402


def test_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": seen["body"]["model"],
            "stop_reason": "tool_use", "stop_sequence": None,
            "content": [
                {"type": "text", "text": "Je remplis le schéma."},
                {"type": "tool_use", "id": "toolu_1", "name": "Signals",
                 "input": {"fatigue_1_5": 4, "sleep_quality_1_5": 1}},
            ],
            "usage": {"input_tokens": 812, "output_tokens": 57,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        })

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm = AnthropicLLM(client=client)
    out = llm.structured(node="extract", tier="small", system="SYS", user="Message: épuisé",
                         schema=Signals)

    body = seen["body"]
    assert seen["url"].endswith("/v1/messages")
    assert body["model"] == "claude-haiku-4-5"
    assert body["tool_choice"] == {"type": "tool", "name": "Signals"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert out.fatigue_1_5 == 4 and out.sleep_quality_1_5 == 1
    assert llm.usage_log[0].input_tokens == 812


def test_full_graph_with_claude_adapter(monkeypatch):
    """Graphe complet avec AnthropicLLM : extract (Haiku) puis coach (Sonnet) (pipeline mono-agent ; la version
    équipe d'agents est testée dans tests/test_v040_agents.py)."""
    monkeypatch.setenv("COACH_AGENTS", "single")
    from datetime import date

    from coach.app import handle_inbound
    from coach.sandbox import build_sandbox
    from coach.schemas import Channel, InboundMessage

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["tools"][0]["name"] == "Signals":
            payload = {"fatigue_1_5": 5, "sleep_quality_1_5": 1, "life_stress_1_5": 4}
        else:
            plan = json.loads(body["messages"][0]["content"])["plan_next_7d"]
            first = {**plan[0], "intensity": "z1", "duration_min": 40, "planned_load": 30}
            payload = {"changes": [{"action": "modify", "session": first, "reason": "nuit blanche"}],
                       "message_to_athlete": "Nuit blanche : footing cool aujourd'hui.",
                       "evidence_ids": [], "is_major_change": False}
        return httpx.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": body["model"],
            "stop_reason": "tool_use", "stop_sequence": None,
            "content": [{"type": "tool_use", "id": "t", "name": body["tools"][0]["name"], "input": payload}],
            "usage": {"input_tokens": 1000, "output_tokens": 100}})

    llm = AnthropicLLM(client=anthropic.Anthropic(
        api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    graph, deps = build_sandbox(date(2026, 9, 30), llm=llm)
    out = handle_inbound(graph, InboundMessage(athlete_id="a", channel=Channel.TELEGRAM,
                                               text="Ma fille a été malade toute la nuit, je suis épuisé"))
    assert calls == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert out["reply"].startswith("Nuit blanche") and deps.repo.plan("a")[0].intensity == "z1"
