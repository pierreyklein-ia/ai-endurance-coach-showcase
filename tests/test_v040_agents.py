"""v0.40 — équipe d'agents : superviseur, spécialistes outillés en parallèle, coach, critique, Œil du coach."""
import json
import threading
import time
from datetime import date, timedelta

import pytest

from coach.agents import eye as E
from coach.agents.offline import critic_checks, route_rules, shortcut
from coach.agents.schemas import CriticVerdict, RoutingDecision, SpecialistNote
from coach.agents.team import run_specialist
from coach.agents.tools import FACTORIES, ToolCtx, toolbox
from coach.app import handle_inbound
from coach.checkpoint import memory_saver
from coach.graph import build_graph
from coach.llm import AgentResult, FallbackLLM, MockLLM
from coach.sandbox import HeuristicLLM, build_sandbox
from coach.schemas import Channel, CoachDecision, InboundMessage, Pain, PlanChange, PlannedSession, Signals
from tests.conftest import PLAN, d
from tests.test_pilot import TODAY as PILOT_TODAY
from tests.test_v037 import ready

DAY = date(2026, 9, 23)


def msg(text, aid="a"):
    return InboundMessage(athlete_id=aid, channel=Channel.WEB, text=text)


def run_sandbox(text, llm=None, agents="multi"):
    graph, deps = build_sandbox(DAY, llm=llm, agents=agents)
    trace = []
    out = handle_inbound(graph, msg(text), trace)
    return out, trace, deps


def upd(trace, node):
    return [u for n, u in trace if n == node]


# --------------------------------------------------------------------------- 1. routage
def test_routing_rules_pick_relevant_specialists():
    sig = Signals(pain=[Pain(location="genou", severity_0_10=6)])
    r = route_rules("Mon genou me fait mal, je fais mon seuil ?", sig)
    assert r.specialists == ["prevention", "charge"] and "douleur genou 6/10" in r.reason
    r = route_rules("Ma sortie longue s'est mal passée, nausées après 2 h", Signals())
    assert set(r.specialists) == {"nutrition", "prevention"}
    assert route_rules("Merci coach", Signals()).specialists == []
    assert route_rules("Quel chrono viser sur la Diag ?", Signals()).specialists == ["course"]
    assert len(route_rules("genou 6/10, nausées, raté 2 jours, chrono de la course ?",
                           Signals(pain=[Pain(location="genou", severity_0_10=6)])).specialists) == 3   # 3 au plus


def test_shortcuts_skip_the_supervisor_llm():
    assert shortcut("J'ai déplacé « Seuil » de mercredi à jeudi.", Signals()).specialists == ["charge"]
    assert shortcut("[Bilan nutrition] 3 h : ça s'est mal passé, nausées", Signals()).specialists == ["nutrition", "prevention"]
    assert shortcut("Comment ça va ?", Signals()) is None
    llm = MockLLM(handlers={"extract": lambda u: Signals(), "coach": lambda u: CoachDecision(changes=[], message_to_athlete="ok")})
    graph, deps = build_sandbox(DAY, llm=llm, agents="multi")
    tr = []
    handle_inbound(graph, msg("J'ai déplacé « Seuil » de mercredi 23/09 à jeudi 24/09."), tr)
    r = upd(tr, "route")[0]["routing"]
    assert r["by"] == "règle" and "supervisor" not in llm.team_calls and r["specialists"] == ["charge"]


def test_supervisor_is_an_llm_decision_with_a_safety_floor():
    """Le superviseur (LLM) décide et c'est journalisé ; s'il oublie Prévention malgré une douleur, le code l'ajoute."""
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(pain=[Pain(location="cheville", severity_0_10=5)]),
        "supervisor": lambda u: RoutingDecision(specialists=["course"], reason="question de course"),
        "coach": lambda u: CoachDecision(changes=[], message_to_athlete="Repos et footing facile."),
    })
    graph, deps = build_sandbox(DAY, llm=llm, agents="multi")
    tr = []
    handle_inbound(graph, msg("Ma cheville tire, quel chrono pour la Diag ?"), tr)
    r = upd(tr, "route")[0]["routing"]
    assert "supervisor" in llm.calls and r["by"] == "superviseur"
    assert r["specialists"] == ["prevention", "course"] and "Prévention : signal de santé" in r["reason"]
    assert {n["name"] for u in upd(tr, "specialist") for n in u["notes"]} == {"prevention", "course"}


# --------------------------------------------------------------------------- 2. outils des spécialistes
def test_every_specialist_tool_runs_on_real_data():
    graph, deps = build_sandbox(DAY)
    sig = Signals(pain=[Pain(location="genou", severity_0_10=6)])
    for key in FACTORIES:
        box = toolbox(key, ToolCtx(deps, "a", sig, "Mon genou me fait mal"))
        for t in box.tools:
            out = box.call(t.name, {})
            assert not (isinstance(out, dict) and "erreur" in out), (key, t.name, out)
            assert t.spec()["input_schema"]["type"] == "object"
        assert len(box.calls) == len(box.tools) and all(c["ms"] >= 0 for c in box.calls)
    box = toolbox("charge", ToolCtx(deps, "a", sig, ""))
    st = box.call("load_state")
    assert st["acwr"] == 0.9 and st["zone_acwr"] == "zone optimale"
    dry = box.call("guardrails_dry_run", {"changes": [{"action": "add", "session_id": "x", "day": str(DAY + timedelta(days=1)),
                                                       "intensity": "vo2", "duration_min": 60, "planned_load": 90}]})
    assert dry["allowed"] is False and any(v["rule"] == "R01_pain" for v in dry["violations"])
    nb = toolbox("nutrition", ToolCtx(deps, "a", Signals(), ""))
    assert nb.call("target_carbs", {"duration_h": 20})["range_g_h"][1] <= 120
    cards = nb.call("knowledge_search", {"tags": ["gi", "gut"]})
    assert cards and all(c["id"].startswith("kb_") for c in cards) and nb.seen_cards == {c["id"] for c in cards}
    assert box.call("outil_inconnu")["erreur"].startswith("outil inconnu")      # jamais d'exception


def test_store_data_reads_the_pilot_database(tmp_path):
    c = ready(tmp_path)
    data = c.pilot.deps.data
    assert data.race_sim_fn is not None                       # branché par le service (simulateur sans verrou)
    assert data.activities(30) and data.weekly_loads(6)
    g = data.main_goal()
    assert g["name"] and g["days_left"] is not None
    assert isinstance(data.adherence(8), list) and isinstance(data.feedback(14), list)
    box = toolbox("charge", ToolCtx(c.pilot.deps, c.service.thread, Signals(), ""))
    p14 = box.call("plan_7d", {"days": 14})
    assert p14["sessions"] and p14["next_days"] and all(x["description"] is not None for x in p14["next_days"])
    assert min(x["day"] for x in p14["next_days"]) > max(x["day"] for x in p14["sessions"])
    fake = {"available": True, "mode": "gpx", "total_km": 175, "headline": {"text": "Arrivée estimée sam. 14:00"},
            "scenarios": [{"label": "cible", "finish_clock": "sam. 14:00", "finish_s": 140000}],
            "points": [{"name": "Cilaos", "km": 67, "cutoff": {"clock": "ven. 12:00", "margin_min": {"prudent": 45},
                                                               "status": "juste"}},
                       {"name": "Marla", "km": 90, "cutoff": None}],
            "climbs": [{"name": "Montée vers Taïbit", "km0": 70, "km1": 76, "gain_m": 800, "grade_pct": 13, "time_s": 9000},
                       {"name": "Montée du km 3", "km0": 3, "km1": 5, "gain_m": 300, "grade_pct": 9, "time_s": 2400}],
            "nutrition": {"target_g_h": 70}}
    data.race_sim_fn = lambda: fake
    sim = data.race_sim()                                      # simulateur : marges aux barrières et montées
    assert sim["cutoffs"] == [{"name": "Cilaos", "km": 67, "clock": "ven. 12:00", "margin_min": 45, "status": "juste"}]
    assert sim["climbs"][0]["name"] == "Montée vers Taïbit" and sim["nutrition"]["target_g_h"] == 70
    note = run_specialist(c.pilot.deps, "course", c.service.thread, "Quelle stratégie pour les barrières ?", Signals())
    assert any("Taïbit" in f for f in note["findings"]) and any("Cilaos" in r for r in note["risks"])


# --------------------------------------------------------------------------- 3. fan-out parallèle / fan-in
class SlowAgents:
    """LLM hors ligne dont chaque spécialiste met 0,4 s (comme une boucle d'outils Claude)."""

    def __init__(self, inner):
        self.inner, self.threads = inner, set()

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def tool_loop(self, **kw):
        self.threads.add(threading.current_thread().name)
        time.sleep(0.4)
        return self.inner.tool_loop(**kw)


def test_specialists_run_in_parallel_then_fan_in():
    llm = SlowAgents(HeuristicLLM(DAY))
    t0 = time.time()
    out, tr, deps = run_sandbox("Mon genou me fait mal 6/10, nausées hier, quel chrono pour la Diag ?", llm=llm)
    took = time.time() - t0
    notes = [n for u in upd(tr, "specialist") for n in u["notes"]]
    assert len(notes) == 3 and took < 1.0                     # 3 × 0,4 s en séquence = 1,2 s
    assert len(llm.threads) >= 2                              # vraiment dans des fils différents
    nodes = [n for n, _ in tr]
    assert nodes.index("team") > max(i for i, n in enumerate(nodes) if n == "specialist")
    assert nodes.index("coach") > nodes.index("team")


def test_zero_specialist_goes_straight_to_the_coach():
    out, tr, deps = run_sandbox("Merci coach, c'était top")
    nodes = [n for n, _ in tr]
    assert "specialist" not in nodes and nodes.index("team") < nodes.index("coach")
    assert upd(tr, "critic")[0]["critic"]["judge"] == "règles"      # rien à confronter : pas d'appel LLM du critique
    assert not [u for u in deps.llm.usage_log if u.node == "critic"]


# --------------------------------------------------------------------------- 4. critique, veto, garde-fous
def _stubborn(decision, critic=None):
    h = {"extract": lambda u: Signals(), "coach": lambda u: decision}
    if critic:
        h["critic"] = critic
    return MockLLM(handlers=h)


def test_critic_revision_loop_is_capped_at_one(make_deps):
    llm = _stubborn(CoachDecision(changes=[], message_to_athlete="Footing facile demain."),
                    critic=lambda u: CriticVerdict(verdict="revise", reasons=["toujours pas assez précis"]))
    deps = make_deps(llm)
    deps.agents = "multi"
    tr = []
    out = handle_inbound(build_graph(deps, checkpointer=memory_saver()), msg("Je fais quoi demain pour ma séance ?"), tr)
    nodes = [n for n, _ in tr]
    assert nodes.count("coach") == 2 and nodes.count("critic") == 2 and "guard" in nodes
    first, second = (u["critic"] for u in upd(tr, "critic"))
    assert first["action"] == "revise" and second["action"] == "guard" and second["revised"]
    assert out["reply"] == "Footing facile demain."


def test_revised_draft_receives_the_critic_feedback(make_deps):
    seen = []

    def coach(u):
        seen.append(json.loads(u).get("critic_feedback"))
        return CoachDecision(changes=[], message_to_athlete="On rattrape samedi !" if len(seen) == 1 else "Pas de rattrapage.")
    deps = make_deps(MockLLM(handlers={"extract": lambda u: Signals(unavailable_dates=[d(-1)]), "coach": coach}))
    deps.agents = "multi"
    tr = []
    out = handle_inbound(build_graph(deps, checkpointer=memory_saver()), msg("J'ai raté hier, je rattrape ?"), tr)
    assert seen[0] is None and any("Rattrapage" in r for r in seen[1])
    assert out["reply"] == "Pas de rattrapage." and upd(tr, "critic")[-1]["critic"]["verdict"] == "ok"


def test_prevention_veto_is_respected_and_guardrails_stay_final(make_deps):
    """Veto (douleur 6/10) : le critique renvoie le brouillon intense ; le coach têtu recommence ; les garde-fous
    bloquent quand même (R01) puis repli prudent : la sécurité ne dépend pas d'un LLM."""
    hard = PLAN[0].model_copy(update={"intensity": "vo2", "duration_min": 60, "planned_load": 90})
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(pain=[Pain(location="genou", severity_0_10=6)]),
        "coach": lambda u: CoachDecision(changes=[PlanChange(action="modify", session=hard, reason="motivation")],
                                         message_to_athlete="Fais ta VMA, ça ira."),
        "critic": lambda u: CriticVerdict(verdict="ok"),              # critique LLM « trompé »
    })
    deps = make_deps(llm)
    deps.agents = "multi"
    tr = []
    out = handle_inbound(build_graph(deps, checkpointer=memory_saver()), msg("Genou 6/10, je fais ma VMA ?"), tr)
    notes = [n for u in upd(tr, "specialist") for n in u["notes"]]
    assert next(n for n in notes if n["name"] == "prevention")["veto"]
    crit = upd(tr, "critic")[0]["critic"]
    assert crit["verdict"] == "revise" and "Veto" in crit["deterministic"][0]     # le code ne se laisse pas tromper
    assert "fallback" in [n for n, _ in tr] and "prudence" in out["reply"]
    assert not any(e["summary"].startswith("🏃 VMA") for e in deps.calendar.events.values())


def test_guardrails_block_what_the_critic_lets_through(make_deps):
    big = [PlannedSession(session_id=f"big{i}", day=d(i + 1), intensity="long", duration_min=300,
                          elevation_gain_m=3000, planned_load=320) for i in range(3)]
    llm = _stubborn(CoachDecision(changes=[PlanChange(action="add", session=s, reason="volume") for s in big],
                                  message_to_athlete="Trois grosses sorties pour finir le bloc."),
                    critic=lambda u: CriticVerdict(verdict="ok"))
    deps = make_deps(llm)
    deps.agents = "multi"
    tr = []
    handle_inbound(build_graph(deps, checkpointer=memory_saver()), msg("On charge la semaine ?"), tr)
    assert all(u["critic"]["verdict"] == "ok" for u in upd(tr, "critic"))
    assert upd(tr, "guard")[0]["verdict"].allowed is False and "fallback" in [n for n, _ in tr]


def test_critic_checks_negations_carbs_unsafe_and_citations():
    ok = CoachDecision(changes=[], message_to_athlete="Pas de rattrapage : on reprend le plan, les séances manquées sont perdues.")
    assert critic_checks(ok, [], set(), Signals(unavailable_dates=[DAY])) == []
    bad = CoachDecision(changes=[], message_to_athlete="On rattrape samedi.")
    assert "Rattrapage" in critic_checks(bad, [], set(), Signals())[0]
    assert "120 g/h" in critic_checks(CoachDecision(changes=[], message_to_athlete="Monte à 140 g/h."), [], set(), Signals())[0]
    assert critic_checks(CoachDecision(changes=[], message_to_athlete="Vise 60 à 90 g/h."), [], set(), Signals()) == []
    assert "dangereux" in critic_checks(CoachDecision(changes=[], message_to_athlete="Prends un ibuprofène."), [], set(),
                                        Signals())[0]
    assert "inconnue" in critic_checks(CoachDecision(changes=[], message_to_athlete="x", evidence_ids=["kb_faux"]), [],
                                       {"kb_vrai"}, Signals())[0]
    veto = [{"name": "prevention", "veto": True, "veto_reason": "douleur", "risk_level": "high"}]
    assert critic_checks(CoachDecision(changes=[], message_to_athlete="Garde ta séance de seuil ce soir."), veto, set(),
                         Signals())
    assert critic_checks(CoachDecision(changes=[], message_to_athlete="Ne fais pas ton seuil : footing facile."), veto,
                         set(), Signals()) == []


# --------------------------------------------------------------------------- 5. mode single / multi
def test_single_mode_keeps_the_v039_pipeline(monkeypatch):
    out, tr, deps = run_sandbox("Ma sortie longue s'est mal passée, nausées", agents="single")
    assert [n for n, _ in tr][:4] == ["triage", "extract", "evidence", "coach"]
    assert not {"route", "specialist", "critic"} & {n for n, _ in tr}
    monkeypatch.setenv("COACH_AGENTS", "single")
    graph, deps = build_sandbox(DAY)
    assert "route" not in graph.get_graph().nodes
    monkeypatch.setenv("COACH_AGENTS", "multi")
    graph, deps = build_sandbox(DAY)
    assert {"route", "specialist", "team", "critic"} <= set(graph.get_graph().nodes)


def test_multi_mode_cites_team_cards_and_costs_more_tokens_only_on_small_tier():
    out_s, tr_s, deps_s = run_sandbox("Ma sortie longue s'est mal passée, nausées après 2 h", agents="single")
    out_m, tr_m, deps_m = run_sandbox("Ma sortie longue s'est mal passée, nausées après 2 h", agents="multi")
    last = upd(tr_m, "coach")[-1]["decision"]
    assert last.evidence_ids and "Côté nutrition" in last.message_to_athlete
    main = lambda llm: [u for u in llm.usage_log if u.tier == "main"]            # noqa: E731
    assert len(main(deps_m.llm)) == len(main(deps_s.llm)) == 1                   # un seul appel au gros modèle
    assert {u.node for u in deps_m.llm.usage_log if u.tier == "small"} >= {"extract", "spec_nutrition", "spec_prevention",
                                                                              "critic"}


# --------------------------------------------------------------------------- 6. repli par agent
class HalfBroken(HeuristicLLM):
    """Claude « réel » simulé : la nutrition tombe en panne (529), les autres agents répondent."""

    def tool_loop(self, *, node, **kw):
        if node == "spec_nutrition":
            raise RuntimeError("Error code: 529 - overloaded")
        return super().tool_loop(node=node, **kw)


def test_fallback_llm_falls_back_agent_by_agent():
    llm = FallbackLLM(HalfBroken(DAY), HeuristicLLM(DAY))
    out, tr, deps = run_sandbox("Ma sortie longue s'est mal passée, nausées après 2 h", llm=llm)
    notes = {n["name"]: n for u in upd(tr, "specialist") for n in u["notes"]}
    assert llm.agent_failures == 1 and llm.failures == 0           # le message du coach reste « Claude »
    assert notes["nutrition"]["tools"][0]["name"] == "repli_hors_ligne" and notes["nutrition"]["findings"]
    assert "surchargé" in notes["nutrition"]["degraded"]
    assert all(t["name"] != "repli_hors_ligne" for t in notes["prevention"]["tools"])


def test_llm_without_agent_method_still_gets_the_team():
    class OnlyStructured:
        def __init__(self):
            self.inner = HeuristicLLM(DAY)
            self.usage_log = self.inner.usage_log

        def structured(self, **kw):
            return self.inner.structured(**kw)
    out, tr, deps = run_sandbox("Nausées sur ma sortie longue", llm=OnlyStructured())
    assert [n for u in upd(tr, "specialist") for n in u["notes"]]


# --------------------------------------------------------------------------- 7. vraie boucle d'outils Anthropic
anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")


def _resp(body, content, inp=500, out=80):
    return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": body["model"],
                                     "stop_reason": "tool_use", "stop_sequence": None, "content": content,
                                     "usage": {"input_tokens": inp, "output_tokens": out}})


def test_anthropic_tool_loop_executes_tools_and_returns_the_final_answer():
    from coach.llm import AnthropicLLM
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(body["messages"]) == 1:
            return _resp(body, [{"type": "text", "text": "Je lis la charge."},
                                {"type": "tool_use", "id": "t1", "name": "load_state", "input": {}}])
        return _resp(body, [{"type": "tool_use", "id": "t2", "name": "reponse_finale", "input": {
            "findings": ["ACWR 0,9"], "recommendations": ["Garder le plan"], "risk_level": "low",
            "evidence_ids": ["kb_acwr_impellizzeri2020"], "confidence": 0.8}}])
    llm = AnthropicLLM(client=anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    graph, deps = build_sandbox(DAY)
    box = toolbox("charge", ToolCtx(deps, "a", Signals(), "ma charge ?"))
    res = llm.tool_loop(node="spec_charge", tier="small", system="SYS", user="{}", tools=box, schema=SpecialistNote)
    assert isinstance(res, AgentResult) and res.output.findings == ["ACWR 0,9"] and res.steps == 2
    assert [c["name"] for c in res.tool_calls] == ["load_state"]
    assert bodies[0]["tool_choice"] == {"type": "any"} and bodies[0]["model"] == "claude-haiku-4-5"
    assert {t["name"] for t in bodies[0]["tools"]} >= {"load_state", "guardrails_dry_run", "reponse_finale"}
    tr = bodies[1]["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "t1" and '"zone_acwr"' in tr["content"]
    assert [u.node for u in llm.usage_log] == ["spec_charge", "spec_charge"]


def test_anthropic_tool_loop_forces_the_final_answer_on_the_last_step():
    from coach.llm import AnthropicLLM
    choices = []

    def handler(request):
        body = json.loads(request.content)
        choices.append(body["tool_choice"])
        if body["tool_choice"]["type"] == "any":
            return _resp(body, [{"type": "tool_use", "id": f"t{len(choices)}", "name": "plan_7d", "input": {}}])
        return _resp(body, [{"type": "tool_use", "id": "f", "name": "reponse_finale", "input": {"findings": ["x"]}}])
    llm = AnthropicLLM(client=anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    graph, deps = build_sandbox(DAY)
    res = llm.tool_loop(node="spec_charge", tier="small", system="S", user="{}", schema=SpecialistNote,
                    tools=toolbox("charge", ToolCtx(deps, "a", Signals(), "")), max_steps=3)
    assert choices == [{"type": "any"}, {"type": "any"}, {"type": "tool", "name": "reponse_finale"}]
    assert res.steps == 3 and len(res.tool_calls) == 2


def test_full_multi_agent_graph_with_the_claude_adapter():
    """Le graphe multi-agents complet sur l'adaptateur Claude (transport simulé) : Haiku pour l'extraction, le
    superviseur, les spécialistes (boucle d'outils) et le critique ; Sonnet seulement pour le coach."""
    from coach.llm import AnthropicLLM
    models = []

    def handler(request):
        body = json.loads(request.content)
        names = [t["name"] for t in body["tools"]]
        models.append((names[-1], body["model"]))
        if names == ["Signals"]:
            return _resp(body, [{"type": "tool_use", "id": "s", "name": "Signals", "input": {
                "fatigue_1_5": 5, "sleep_quality_1_5": 1}}])
        if names == ["RoutingDecision"]:
            return _resp(body, [{"type": "tool_use", "id": "r", "name": "RoutingDecision", "input": {
                "specialists": ["prevention"], "reason": "nuit blanche"}}])
        if "reponse_finale" in names:
            if len(body["messages"]) == 1:
                return _resp(body, [{"type": "tool_use", "id": "w", "name": names[0], "input": {}}])
            return _resp(body, [{"type": "tool_use", "id": "f", "name": "reponse_finale", "input": {
                "findings": ["Nuit blanche, fatigue 5/5"], "recommendations": ["Pas d'intensité"], "risk_level": "high",
                "evidence_ids": ["kb_sommeil_fullagar2015"], "veto": True, "veto_reason": "nuit blanche"}}])
        if names == ["CriticVerdict"]:
            return _resp(body, [{"type": "tool_use", "id": "c", "name": "CriticVerdict", "input": {"verdict": "ok"}}])
        plan = json.loads(body["messages"][0]["content"])["plan_next_7d"]
        first = {**plan[0], "intensity": "z1", "duration_min": 40, "planned_load": 30}
        return _resp(body, [{"type": "tool_use", "id": "k", "name": "CoachDecision", "input": {
            "changes": [{"action": "modify", "session": first, "reason": "douleur"}],
            "message_to_athlete": "Footing très facile aujourd'hui, dors bien ce soir.",
            "evidence_ids": ["kb_sommeil_fullagar2015"], "is_major_change": False}}])
    llm = AnthropicLLM(client=anthropic.Anthropic(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    out, tr, deps = run_sandbox("Nuit blanche avec le bébé, je suis épuisé, je fais mon seuil ?", llm=llm)
    assert out["reply"].startswith("Footing très facile") and deps.repo.plan("a")[0].intensity == "z1"
    sonnet = [n for n, m in models if m == "claude-sonnet-5"]
    assert sonnet == ["CoachDecision"] and all(m == "claude-haiku-4-5" for n, m in models if n != "CoachDecision")
    note = upd(tr, "specialist")[0]["notes"][0]
    assert note["veto"] and note["tools"][0]["name"] == "wellbeing_signals" and note["evidence_ids"]


# --------------------------------------------------------------------------- 8. trace dans le chat (API)
def test_chat_message_meta_shows_the_team(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_AGENTS", "multi")
    c = ready(tmp_path)
    r = c.post("/api/chat", json={"text": "Ma sortie longue s'est mal passée, nausées après 2 h. Je mange quoi ?"}).json()
    meta = r["messages"][-1]["meta"]
    assert meta["routing"]["specialists"] and meta["routing"]["reason"] and meta["routing"]["by"]
    a = meta["agents"][0]
    assert set(a) >= {"name", "label", "icon", "reason", "tools", "risk_level", "veto", "evidence_ids", "cost_eur", "ms"}
    assert all(set(t) == {"name", "ms"} for t in a["tools"])
    assert meta["critic"]["verdict"] in ("ok", "revise") and "revised" in meta["critic"]
    assert "superviseur" in meta["trace"] and meta["cost_by_agent"]["coach"] > 0
    assert "Nutrition" in {x["label"] for x in meta["agents"]}


# --------------------------------------------------------------------------- 9. Œil du coach
def test_eye_is_locked_below_level_7(tmp_path):
    c = ready(tmp_path)
    c.service.store.set("eye_welcome_used", "2026-09-01")                  # l'analyse offerte est déjà prise
    st = c.get("/api/eye").json()
    assert st["required_level"] == E.EYE_LEVEL == 7 and not st["unlocked"] and 0 < st["progress_pct"] < 100
    r = c.post("/api/eye")
    assert r.status_code == 409 and "niveau 7" in r.json()["error"]


def test_eye_report_shape_chat_message_and_30_day_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "EYE_LEVEL", 1)
    c = ready(tmp_path)
    c.service.coach_async = False
    r = c.post("/api/eye")
    assert r.status_code == 200 and r.json()["started"]
    st = c.get("/api/eye").json()
    rep = st["last"]["report"]
    assert set(rep) == {"summary", "strengths", "weaknesses", "risks", "nutrition", "race", "priorities", "to_measure",
                        "next_review"}
    assert rep["to_measure"] and st["history"][-1]["day"] == st["last"]["day"]
    assert len(rep["priorities"]) == 3 and all(p["action"] and p["why"] and p["cards"] for p in rep["priorities"])
    assert {a["name"] for a in st["last"]["agents"]} == {"charge", "nutrition", "course", "prevention"}
    assert st["last"]["critic"]["verdict"] == "ok" and st["last"]["cost_eur"] > 0
    assert not st["available"] and st["next_at"] == (PILOT_TODAY + timedelta(days=30)).isoformat()
    last = c.get("/api/chat").json()["messages"][-1]
    assert last["role"] == "coach" and last["meta"]["eye"] and "Progrès" in last["text"] and len(last["meta"]["agents"]) == 4
    r = c.post("/api/eye")
    assert r.status_code == 409 and "30 jours" in r.json()["error"]


def test_eye_runs_in_the_background_with_typing(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "EYE_LEVEL", 1)
    c = ready(tmp_path, llm=SlowAgents(HeuristicLLM(PILOT_TODAY)))
    t0 = time.time()
    r = c.post("/api/eye").json()
    assert time.time() - t0 < 0.4 and r["typing"] and r["running"]
    assert c.get("/api/chat").json()["typing"] is True
    assert c.post("/api/eye").status_code == 409                      # déjà en cours
    assert c.service.wait_coach(20)
    st = c.get("/api/eye").json()
    assert not st["running"] and st["last"]["report"]["priorities"] and not c.get("/api/chat").json()["typing"]


def test_eye_respects_the_daily_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "EYE_LEVEL", 1)
    c = ready(tmp_path)
    c.pilot.settings.daily_budget_eur = 0.0
    r = c.post("/api/eye")
    assert r.status_code == 409 and "Budget" in r.json()["error"]


def test_eye_graph_consults_all_four_specialists_in_eye_mode():
    graph, deps = build_sandbox(DAY)
    res = E.run_eye(deps, "a")
    assert [n["name"] for n in res["notes"]] == ["prevention", "charge", "nutrition", "course"]
    assert all(n["node"].startswith("eye_spec_") for n in res["notes"])
    assert any(t["name"] == "plan_adherence" for t in res["notes"][1]["tools"])
    assert {u.node for u in deps.llm.usage_log} >= {"eye_spec_charge", "eye_report", "eye_critic"}
    assert [u.tier for u in deps.llm.usage_log if u.node == "eye_report"] == ["main"]


# --------------------------------------------------------------------------- 10. évaluation hors ligne
def test_offline_agents_evaluation_meets_the_thresholds():
    from eval.agents_eval import evaluate
    res = evaluate()
    m, s = res["summary"]["multi"], res["summary"]["single"]
    assert m["routing"]["recall"] >= 0.8 and m["routing"]["precision"] >= 0.75
    assert m["safety"]["final"] == 0 and s["safety"]["final"] == 0          # garde-fous : autorité finale
    assert m["safety"]["to_guardrails"] <= s["safety"]["to_guardrails"]
    assert m["critic"]["catch_rate"] >= 0.8 and m["critic"]["false_alarms"] == 0
    assert m["short_circuits_ok"] == s["short_circuits_ok"] == 1.0
    assert m["groundedness"]["replies_with_card"] > s["groundedness"]["replies_with_card"]
    assert m["groundedness"]["invalid_citations"] == 0
    assert m["must_mention"] >= s["must_mention"]


def test_finops_multi_agent_cost_model():
    from finops import TeamBudget, per_message_eur
    s, m = per_message_eur(TeamBudget(multi=False)), per_message_eur(TeamBudget())
    assert set(m) >= {"superviseur", "specialistes", "critique", "coach", "total"}
    assert s["total"] < m["total"] < 2 * s["total"] and m["coach"] > 0.5 * m["total"]   # le gros modèle reste le poste n°1
    from finops import eye_eur
    e = eye_eur()
    assert set(e) == {"specialistes", "rapport", "critique", "total"} and 0.02 < e["total"] < 0.15   # < 15 c€ / analyse


# --------------------------------------------------------------------------- 11. compléments v0.40
def test_specialist_note_contract_findings_recommendations_risks_evidence_confidence():
    graph, deps = build_sandbox(DAY)
    sig = Signals(pain=[Pain(location="genou", severity_0_10=6)], fatigue_1_5=4)
    for key in ("prevention", "charge", "nutrition", "course"):
        n = run_specialist(deps, key, "a", "Genou 6/10, nausées, je rajoute une séance ? chrono Diag ?", sig)
        SpecialistNote.model_validate({k: n[k] for k in ("findings", "recommendations", "risks", "risk_level",
                                                         "evidence_ids", "confidence", "veto", "veto_reason")})
        assert n["findings"] and n["recommendations"] and n["evidence_ids"] and 0 <= n["confidence"] <= 1
        assert not n["invalid_cards"], key
    prev = run_specialist(deps, "prevention", "a", "Genou 6/10", sig)
    assert prev["veto"] and any("genou" in r for r in prev["risks"])


def test_offline_tool_loop_is_deterministic_and_executes_tools():
    from coach.agents.tools import ToolCtx, toolbox
    graph, deps = build_sandbox(DAY)
    runs = []
    for _ in range(2):
        box = toolbox("nutrition", ToolCtx(deps, "a", Signals(), "nausées sur la sortie longue"))
        res = HeuristicLLM(DAY).tool_loop(node="spec_nutrition", tier="small", system="S",
                                          user=json.dumps({"message": "nausées sur la sortie longue"}), tools=box,
                                          schema=SpecialistNote)
        assert isinstance(res, AgentResult) and [c["name"] for c in res.tool_calls] == [c["name"] for c in box.calls]
        runs.append(res.output.model_dump())
    assert runs[0] == runs[1] and "nutrition_logs" in [c["name"] for c in box.calls]


def test_team_turn_never_writes_from_a_specialist(tmp_path, monkeypatch):
    """Les outils sont en lecture seule : aucune écriture SQL pendant un spécialiste (chat et usage : hors équipe)."""
    import coach.agents.team as T
    from coach.pilot.store import _SafeConn
    flag, writes, inside = threading.local(), [], []
    real_exec, real_run = _SafeConn.execute, T.run_specialist

    def execute(self, sql, args=()):
        if sql.lstrip().split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE", "REPLACE"):
            (inside if getattr(flag, "on", False) else writes).append(sql.split("(")[0][:40])
        return real_exec(self, sql, args)

    def run(*a, **kw):
        flag.on = True
        try:
            return real_run(*a, **kw)
        finally:
            flag.on = False

    monkeypatch.setattr(_SafeConn, "execute", execute)
    monkeypatch.setattr(T, "run_specialist", run)
    monkeypatch.setenv("COACH_AGENTS", "multi")
    c = ready(tmp_path)
    c.service.coach_async = False
    writes.clear()
    r = c.post("/api/chat", json={"text": "Genou 6/10 depuis hier, nausées sur la longue, quel chrono pour mon objectif ?"})
    meta = r.json()["messages"][-1]["meta"]
    assert len(meta["agents"]) >= 2 and inside == []
    assert writes                                           # le chat et le coût du jour, hors spécialistes


def test_budget_exhausted_means_no_team_call(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_AGENTS", "multi")
    c = ready(tmp_path)
    c.service.coach_async = False
    c.pilot.settings.daily_budget_eur = 0.0
    n0 = len(c.pilot.deps.llm.usage_log)
    r = c.post("/api/chat", json={"text": "Genou 6/10, je fais mon seuil ?"}).json()
    assert len(c.pilot.deps.llm.usage_log) == n0                 # ni superviseur, ni spécialiste, ni coach
    assert not (r["messages"][-1].get("meta") or {}).get("agents")


def test_eye_level_and_cooldown_env_overrides(monkeypatch):
    import importlib
    monkeypatch.setenv("COACH_EYE_LEVEL", "3")
    monkeypatch.setenv("COACH_EYE_EVERY_DAYS", "14")
    try:
        importlib.reload(E)
        assert (E.EYE_LEVEL, E.EYE_COOLDOWN_DAYS) == (3, 14)
        monkeypatch.setenv("COACH_EYE_LEVEL", "pas un nombre")
        importlib.reload(E)
        assert E.EYE_LEVEL == 7
    finally:
        monkeypatch.delenv("COACH_EYE_LEVEL")
        monkeypatch.delenv("COACH_EYE_EVERY_DAYS")
        importlib.reload(E)
    assert (E.EYE_LEVEL, E.EYE_COOLDOWN_DAYS) == (7, 30)


def test_settings_show_the_agent_mode_and_the_team(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_AGENTS", "single")
    c = ready(tmp_path)
    a = c.get("/api/about").json()["agents"]
    assert a["mode"] == "single" and [t["key"] for t in a["team"]] == ["coach"]
    keys = [t["key"] for t in a["all"]]
    assert keys == ["supervisor", "prevention", "charge", "nutrition", "course", "coach", "critic"]
    charge = next(t for t in a["all"] if t["key"] == "charge")
    assert "guardrails_dry_run" in charge["tools"] and charge["tier"] == "small" and a["eye_level"] == E.EYE_LEVEL
    monkeypatch.setenv("COACH_AGENTS", "multi")
    assert len(c.get("/api/about").json()["agents"]["team"]) == 7


def test_background_move_and_nutrition_turns_use_the_team(tmp_path, monkeypatch):
    """Réactions du coach en tâche de fond (déplacement, bilan nutrition) : l'équipe est consultée aussi."""
    from datetime import date as _d

    from tests.test_v038 import LONG, SID, add_run
    monkeypatch.setenv("COACH_AGENTS", "multi")
    c = ready(tmp_path)
    assert c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"}).status_code == 200
    assert c.service.wait_coach(20)
    moved = c.get("/api/chat").json()["messages"][-1]
    assert moved["role"] == "coach" and [a["name"] for a in moved["meta"]["agents"]] == ["charge"]
    assert moved["meta"]["routing"]["by"] == "règle"
    add_run(c, _d(2026, 10, 4), 130)
    body = {"session_id": LONG, "items": [{"id": "gel", "qty": 3}], "water_ml": 500, "digestion": 4,
            "symptoms": ["nausee"], "comment": ""}
    assert c.post("/api/nutrition", json=body).status_code == 200
    assert c.service.wait_coach(20)
    nut = c.get("/api/chat").json()["messages"][-1]
    assert nut["role"] == "coach" and {a["name"] for a in nut["meta"]["agents"]} >= {"nutrition", "prevention"}


# --------------------------------------------------------------------------- v0.41 : première analyse offerte
def test_first_eye_analysis_is_free_then_locked_until_level_7(tmp_path):
    c = ready(tmp_path)
    c.service.coach_async = False
    st = c.get("/api/eye").json()
    assert not st["unlocked"] and st["free_available"] and st["available"]
    r = c.post("/api/eye")
    assert r.status_code == 200 and r.json()["started"]
    st = c.get("/api/eye").json()
    assert st["last"]["free"] and st["last"]["report"]["priorities"]          # le rapport reste consultable
    assert not st["free_available"] and not st["available"] and not st["unlocked"]
    last = c.get("/api/chat").json()["messages"][-1]
    assert "analyse offerte" in last["text"] and "niveau 7" in last["text"]
    r = c.post("/api/eye")
    assert r.status_code == 409 and "niveau 7" in r.json()["error"]


def test_failed_free_analysis_is_not_consumed(tmp_path, monkeypatch):
    c = ready(tmp_path)
    c.service.coach_async = False
    monkeypatch.setattr(E, "run_eye", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("panne")))
    c.post("/api/eye")
    assert c.get("/api/eye").json()["free_available"]
