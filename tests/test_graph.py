"""Scénarios bout-en-bout du graphe avec LLM mocké (0 €, déterministe)."""
from coach.app import handle_inbound
from coach.checkpoint import memory_saver
from coach.graph import build_graph
from coach.llm import MockLLM
from coach.schemas import Channel, CoachDecision, InboundMessage, PlanChange, PlannedSession, Signals
from tests.conftest import PLAN, d


def msg(text):
    return InboundMessage(athlete_id="ath_001", channel=Channel.TELEGRAM, text=text)


def run(deps, *texts):
    graph = build_graph(deps, checkpointer=memory_saver())
    return [handle_inbound(graph, msg(t)) for t in texts]


def test_noise_costs_zero_tokens(make_deps):
    llm = MockLLM(handlers={})
    deps = make_deps(llm)
    (out,) = run(deps, "ok merci 👍")
    assert out["reply"] == "👍"
    assert llm.calls == []


def test_emergency_short_circuits_llm(make_deps):
    llm = MockLLM(handlers={})
    deps = make_deps(llm)
    (out,) = run(deps, "J'ai eu une douleur à la poitrine dans la côte")
    assert llm.calls == []
    assert "15" in out["reply"]
    assert all(op.startswith("deleted:") for op in out["calendar_ops"])


def test_life_stress_is_not_dropped_and_lowers_load(make_deps):
    easy = PLAN[0].model_copy(update={"intensity": "z1", "duration_min": 40, "planned_load": 30})
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(fatigue_1_5=5, sleep_quality_1_5=1, life_stress_1_5=4),
        "coach": lambda u: CoachDecision(
            changes=[PlanChange(action="modify", session=easy, reason="nuit blanche")],
            message_to_athlete="Nuit courte : on remplace le seuil par un footing cool.",
        ),
    })
    deps = make_deps(llm)
    (out,) = run(deps, "Ma fille a été malade toute la nuit, je suis épuisé")
    assert llm.calls == ["extract", "coach"]
    assert out["calendar_ops"] == ["patched:s1"] or out["calendar_ops"] == ["inserted:s1"]
    assert "footing" in out["reply"]


def test_catch_up_is_blocked_then_fallback(make_deps):
    extra = [PlannedSession(session_id=f"x{i}", day=d(i + 1), intensity="vo2",
                            duration_min=80, planned_load=150) for i in range(2)]
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(unavailable_dates=[d(-3), d(-2), d(-1)]),
        # LLM "têtu" : propose du rattrapage à chaque essai
        "coach": lambda u: CoachDecision(
            changes=[PlanChange(action="keep", session=p, reason="") for p in PLAN]
            + [PlanChange(action="add", session=s, reason="rattrapage") for s in extra],
            message_to_athlete="On rattrape ce week-end !",
        ),
    })
    deps = make_deps(llm)
    (out,) = run(deps, "J'ai raté 3 jours, je peux rattraper ce week-end ?")
    assert llm.calls == ["extract", "coach", "coach"]           # 1 retry max
    # v0.50.1 : sans signal de risque, le plan est GARDÉ (avant : toute la semaine passait en footing Z1)
    assert "Je garde ton plan tel quel" in out["reply"] and "pas de rattrapage" in out["reply"]
    assert not any(e["summary"].startswith("🏃 Footing Z1") for e in deps.calendar.events.values())
    assert not any("x0" in k or "x1" in k for k in deps.calendar.events)


def test_major_change_requires_confirmation(make_deps):
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(fatigue_1_5=4),
        "coach": lambda u: CoachDecision(
            changes=[PlanChange(action="cancel", session=PLAN[3], reason="fatigue")],
            message_to_athlete="Je propose d'annuler la sortie longue de samedi.",
            is_major_change=True,
        ),
    })
    deps = make_deps(llm)
    ask, done = run(deps, "Grosse fatigue cette semaine, jambes lourdes", "oui")
    assert "oui / non" in ask["ask"]
    assert done["calendar_ops"] == ["deleted:s4"]
    assert llm.calls == ["extract", "coach"]   # le "oui" ne repasse PAS par le triage


def test_major_change_refused(make_deps):
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(fatigue_1_5=4),
        "coach": lambda u: CoachDecision(
            changes=[PlanChange(action="cancel", session=PLAN[3], reason="fatigue")],
            message_to_athlete="J'annule la sortie longue ?", is_major_change=True),
    })
    _, done = run(make_deps(llm), "Jambes lourdes", "non")
    assert done["reply"] == "OK, je garde le plan actuel."
    assert done["calendar_ops"] == []


def test_unclear_consent_is_asked_again(make_deps):
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(fatigue_1_5=4),
        "coach": lambda u: CoachDecision(
            changes=[PlanChange(action="cancel", session=PLAN[3], reason="fatigue")],
            message_to_athlete="J'annule la sortie longue ?", is_major_change=True),
    })
    ask1, ask2, done, after = run(make_deps(llm), "Jambes lourdes", "oui mais pas samedi",
                                  "ouais vas-y", "ok merci")
    assert "oui / non" in ask1["ask"] and "pas bien compris" in ask2["ask"]
    assert done["calendar_ops"] == ["deleted:s4"]
    assert after["reply"] == "👍"          # le thread est bien sorti de l'interrupt
