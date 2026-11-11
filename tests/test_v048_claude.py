"""v0.48 : « Claude est indisponible » sans détail ; requêtes réellement envoyées à l'API ; « Tester Claude » ;
vocabulaire simple / expert ; clés de l'espace invité."""
import dataclasses
import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from coach import vocab
from coach.llm import (
    MODELS,
    AnthropicLLM,
    FallbackLLM,
    TruncatedOutput,
    _detail,
    _reason,
    tool_schema,
)
from coach.pilot.runtime import build_pilot
from coach.sandbox import HeuristicLLM
from coach.schemas import CoachDecision
from coach.web.app import create_app
from coach.web.service import CoachService
from tests.test_onboarding import CODE, SECRET, FakeConnector, FakeStrava, chat
from tests.test_pilot import ST
from tests.test_v048_partner import GOAL_TEXT, PROFILE_TEXT, TODAY, club_runner

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------ faux client Anthropic
def _usage():
    return NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _check_request(kw: dict) -> None:
    """Ce que l'API Messages exige (vérifié sur chaque requête du faux client)."""
    assert isinstance(kw["model"], str) and kw["model"]
    assert isinstance(kw["max_tokens"], int) and kw["max_tokens"] >= 1024
    assert kw["system"][0]["type"] == "text" and kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    tools = kw["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    assert sum(1 for t in tools if "cache_control" in t) <= 1 and ("cache_control" not in tools[0] or len(tools) == 1
                                                                    or tools[-1].get("cache_control"))
    for t in tools:
        assert set(t) <= {"name", "description", "input_schema", "cache_control"}
        assert t["input_schema"]["type"] == "object"
        txt = json.dumps(t["input_schema"])
        assert "$ref" not in txt and "$defs" not in txt              # schéma autonome
    tc = kw["tool_choice"]
    assert tc["type"] in ("any", "tool", "auto") and (tc["type"] != "tool" or tc["name"] in names)
    for m in kw["messages"]:
        assert m["role"] in ("user", "assistant")
    assert kw["messages"][0]["role"] == "user"


class FakeAnthropic:
    """client.messages.create(**kw) : vérifie la requête, puis répond par le script `replies` (fonctions kw -> NS)."""

    def __init__(self, replies):
        self.requests, self.replies = [], list(replies)
        outer = self

        class Messages:
            def create(self, **kw):
                _check_request(kw)
                outer.requests.append(kw)
                r = outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r(kw) if callable(r) else r
        self.messages = Messages()


def tool_reply(name, data, stop="tool_use"):
    return NS(content=[NS(type="text", text="ok"), NS(type="tool_use", id="toolu_1", name=name, input=data)],
              stop_reason=stop, usage=_usage())


class Ping(BaseModel):
    ok: bool
    message: str = ""


def test_tool_schema_inlines_defs():
    sch = tool_schema(CoachDecision)
    txt = json.dumps(sch)
    assert "$ref" not in txt and "$defs" not in txt and sch["type"] == "object"
    assert "properties" in sch["properties"]["changes"]["items"]


def test_structured_request_shape_and_main_max_tokens():
    fake = FakeAnthropic([lambda kw: tool_reply(kw["tools"][0]["name"], {"changes": [], "message_to_athlete": "Salut"})])
    llm = AnthropicLLM(client=fake)
    out = llm.structured(node="coach", tier="main", system="SYS", user="hello", schema=CoachDecision)
    kw = fake.requests[0]
    assert out.message_to_athlete == "Salut"
    assert kw["model"] == MODELS["main"] and kw["max_tokens"] >= 4096          # 1024 coupait la décision du coach
    assert kw["tool_choice"] == {"type": "tool", "name": "CoachDecision"}


def test_truncated_output_is_retried_with_more_tokens_then_reported():
    fake = FakeAnthropic([tool_reply("Ping", {}, stop="max_tokens"), tool_reply("Ping", {"ok": True})])
    llm = AnthropicLLM(client=fake)
    assert llm.structured(node="x", tier="small", system="S", user="u", schema=Ping).ok
    assert fake.requests[1]["max_tokens"] == 2 * fake.requests[0]["max_tokens"]
    fake = FakeAnthropic([tool_reply("Ping", {}, stop="max_tokens")] * 4)
    with pytest.raises(TruncatedOutput):
        AnthropicLLM(client=fake).structured(node="x", tier="main", system="S", user="u", schema=Ping)
    assert fake.requests[-1]["max_tokens"] == 8192


def test_tool_loop_request_shapes():
    from coach.agents.tools import Tool, ToolBox

    class Arg(BaseModel):
        jours: int = 7
    box = ToolBox([Tool("activites", "Sorties récentes", lambda jours: [{"j": jours}], Arg),
                   Tool("charge", "Charge", lambda: {"acwr": 1.0})])
    replies = [lambda kw: NS(content=[NS(type="tool_use", id="t1", name="activites", input={"jours": 3})],
                             stop_reason="tool_use", usage=_usage()),
               lambda kw: tool_reply("reponse_finale", {"ok": True, "message": "fini"})]
    fake = FakeAnthropic(replies)
    res = AnthropicLLM(client=fake).tool_loop(node="agent", tier="small", system="S", user="u", tools=box, schema=Ping)
    assert res.output.ok and res.steps == 2 and [c["name"] for c in res.tool_calls] == ["activites"]
    a, b = fake.requests
    assert a["tool_choice"] == {"type": "any"} and a["tools"][-1]["name"] == "reponse_finale"
    assert a["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert b["messages"][1]["role"] == "assistant" and b["messages"][1]["content"][0]["type"] == "tool_use"
    assert b["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(
        [{"j": 3}], ensure_ascii=False)}
    # dernier aller-retour : réponse finale forcée
    fake = FakeAnthropic([lambda kw: NS(content=[NS(type="tool_use", id="t", name="charge", input={})],
                                        stop_reason="tool_use", usage=_usage())] * 2
                         + [lambda kw: tool_reply("reponse_finale", {"ok": True})])
    AnthropicLLM(client=fake).tool_loop(node="agent", tier="small", system="S", user="u", tools=box, schema=Ping,
                                        max_steps=3)
    assert fake.requests[-1]["tool_choice"] == {"type": "tool", "name": "reponse_finale"}


# ------------------------------------------------------------------------ erreurs lisibles
def _api_error(cls_name: str, status: int, message: str):
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json={"type": "error", "error": {"type": "x", "message": message}})
    return getattr(anthropic, cls_name)(message, response=resp, body={"type": "error", "error": {"message": message}})


def test_specific_reasons():
    m = "claude-sonnet-5"
    assert "clé API Claude" in _reason(_api_error("AuthenticationError", 401, "invalid x-api-key"), m)
    assert "accès refusé" in _reason(_api_error("PermissionDeniedError", 403, "no access"), m)
    assert _reason(_api_error("NotFoundError", 404, "model: claude-sonnet-5"), m) == "modèle Claude inconnu (claude-sonnet-5)"
    r = _reason(_api_error("BadRequestError", 400, "tools.0.input_schema: invalid key sk-ant-SECRET123456"), m)
    assert r.startswith("requête refusée par Claude : tools.0.input_schema") and "SECRET" not in r
    assert "crédit" in _reason(_api_error("BadRequestError", 400, "Your credit balance is too low"), m)
    assert "limite" in _reason(_api_error("RateLimitError", 429, "rate_limit_error"), m)
    assert "trop longue" in _reason(TruncatedOutput("coach : coupée"), m)
    assert "réseau" in _reason(TimeoutError("timeout"), m)
    d = _detail(_api_error("NotFoundError", 404, "model: claude-sonnet-5 x-api-key: sk-ant-abc"), m, "coach")
    assert d.startswith("coach · claude-sonnet-5 · NotFoundError 404") and "sk-ant" not in d


def _service(tmp_path, client):
    tmp_path.mkdir(parents=True, exist_ok=True)
    llm = FallbackLLM(AnthropicLLM(client=client), HeuristicLLM(TODAY))
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST), llm=llm)
    svc = CoachService(p, connector=FakeConnector(), strava_factory=lambda: FakeStrava(club_runner()),
                       background=False)
    c = TestClient(create_app(svc, passcode=CODE, secret=SECRET, background=False))
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.service = svc
    return c


def test_degraded_message_carries_a_specific_reason_and_detail(tmp_path, caplog):
    err = _api_error("NotFoundError", 404, "model: claude-sonnet-5")
    c = _service(tmp_path, FakeAnthropic([err] * 40))
    c.service.store.set("onboarded", True)
    caplog.set_level("WARNING", logger="coach.llm")
    r = chat(c, "Comment je dois courir demain ?")
    m = r["messages"][-1]
    assert "Réponse simplifiée : modèle Claude inconnu (" in m["text"]
    assert m["meta"]["degraded_detail"].startswith(("extract · ", "coach · ")) and "NotFoundError 404" in \
        m["meta"]["degraded_detail"]
    assert any("nœud" in r.getMessage() and "NotFoundError" in r.getMessage() for r in caplog.records)


def test_claude_check_endpoint(tmp_path):
    def ping(kw):
        return tool_reply(kw["tools"][0]["name"], {"ok": True, "message": "bonjour"})
    replies = [ping, ping,
               lambda kw: NS(content=[NS(type="tool_use", id="t1", name="date_du_jour", input={})],
                             stop_reason="tool_use", usage=_usage()),
               lambda kw: tool_reply("reponse_finale", {"ok": True, "message": "2026-11-04"})]
    c = _service(tmp_path, FakeAnthropic(replies))
    r = c.post("/api/about/claude-check").json()
    assert r["ok"] and [s["model"] for s in r["steps"]] == [MODELS["small"], MODELS["main"], MODELS["small"]]
    c2 = _service(tmp_path / "b", FakeAnthropic([_api_error("AuthenticationError", 401, "invalid x-api-key")] * 3))
    r = c2.post("/api/about/claude-check").json()
    assert not r["ok"] and all("clé API Claude" in s["message"] for s in r["steps"])
    # mode hors ligne : il le dit
    (tmp_path / "c").mkdir()
    p = build_pilot(home=tmp_path / "c", today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    svc = CoachService(p, background=False)
    assert not svc.check_claude()["ok"] and "pas activé" in svc.check_claude()["steps"][0]["message"]


# ------------------------------------------------------------------------ vocabulaire
def test_glossary_and_simple_titles():
    terms = {t["term"] for t in vocab.terms_in("VMA courte (30/30)", "20 min d'échauffement, 2 × (8 × 30 s rapide / "
                                                                     "30 s trot) à 4:40/km, 10 min de retour au calme.")}
    assert {"VMA", "30/30", "Échauffement", "Retour au calme"} <= terms
    assert "Seuil" in {t["term"] for t in vocab.terms_in("Seuil", "3 × 8 min au seuil")}
    assert vocab.simple_title("Seuil") == "Allure soutenue (seuil)"
    assert "1 h" in next(t["text"] for t in vocab.terms_in("Seuil") if t["term"] == "Seuil")


def test_vocabulary_simple_for_her_expert_for_the_owner(tmp_path):
    from tests.test_onboarding import long_runner
    c = _service(tmp_path, FakeAnthropic([]))
    svc = c.service
    svc.pilot.deps.llm = HeuristicLLM(TODAY)
    assert vocab.get(svc.store) == "simple"
    c.post("/api/strava/connect")
    chat(c, GOAL_TEXT); chat(c, "Oui, c'est ça"); chat(c, PROFILE_TEXT); chat(c, "Pas de limite"); chat(c, "Ça me va")
    assert vocab.get(svc.store) == "simple" and c.get("/api/coach").json()["vocabulary"] == "simple"
    assert "Vocabulaire SIMPLE" in svc.pilot.deps.persona()
    week = c.get("/api/week", params={"start": (TODAY + timedelta(days=5)).isoformat()}).json()
    club = next(s for d in week["days"] for s in d["sessions"] if s["title"].startswith("Séance du club"))
    assert any(t["term"] == "Fractionné" for t in club["terms"])
    # « c'est quoi le seuil ? » ne la fait pas passer en expert ; « je fais ma VMA » si
    chat(c, "c'est quoi le seuil ?")
    assert vocab.get(svc.store) == "simple"
    chat(c, "Mardi j'ai fait ma VMA au club, 10 × 400")
    assert vocab.get(svc.store) == "expert"
    # Réglages : le choix de l'athlète prime, et ne vaut pas « coach choisi »
    r = c.post("/api/coach", json={"gender": "femme", "personality": "bienveillant", "name": "Léa", "address": "tu",
                                   "vocabulary": "simple"}).json()
    assert r["vocabulary"] == "simple"
    chat(c, "Mon seuil c'est 5:10")
    assert vocab.get(svc.store) == "simple"
    assert "Vocabulaire SIMPLE" not in vocab.prompt_block("expert")
    # le compte du propriétaire (ultras, gros volume) : expert, figé au démarrage
    (tmp_path / "owner").mkdir()
    p = build_pilot(home=tmp_path / "owner", today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    from coach.diagnosis import diagnose
    from coach.onboarding import Profile
    p.store.set("diag_full", diagnose(long_runner(TODAY), TODAY).model_dump(mode="json"))
    p.store.set("profile", Profile(goal_name="Diagonale des Fous", goal_date=date(2027, 10, 14)).model_dump(mode="json"))
    owner = CoachService(p, background=False)
    assert owner.store.get(vocab.KEY) == "expert"
    assert "Vocabulaire SIMPLE" not in owner.pilot.deps.persona()


# ------------------------------------------------------------------------ espace invité
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_add_invite_copies_claude_key_with_export_and_quotes(tmp_path):
    shutil.copy(ROOT / "deploy" / "add-invite.sh", tmp_path / "add-invite.sh")
    (tmp_path / "app.env").write_text("export ANTHROPIC_API_KEY=\"sk-ant-test-123\"\r\n"
                                      "  COACH_STRAVA_CLIENT_ID='42'\nCOACH_MODEL_MAIN=claude-sonnet-5\n"
                                      "COACH_BACKUP_REMOTE=secret\n")
    (tmp_path / "invite.env").write_text("COACH_WEB_PASSCODE=abcdefgh\nCOACH_DAILY_BUDGET_EUR=0.5\n"
                                         "ANTHROPIC_API_KEY=ancienne\n")
    out = subprocess.run(["bash", str(tmp_path / "add-invite.sh"), "--cles"], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    env = (tmp_path / "invite.env").read_text().splitlines()
    assert "ANTHROPIC_API_KEY=sk-ant-test-123" in env and "COACH_STRAVA_CLIENT_ID=42" in env
    assert "COACH_WEB_PASSCODE=abcdefgh" in env and not any("ancienne" in x or "BACKUP" in x for x in env)
    assert sum(1 for x in env if x.startswith("ANTHROPIC_API_KEY=")) == 1
