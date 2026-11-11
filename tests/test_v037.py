"""v0.37 — objectifs classés (principal, secondaires, projets) et déplacement d'une séance (glisser-déposer)."""
from datetime import date, timedelta

from coach.goals import goals_list
from coach.onboarding import Event, Profile
from coach.schemas import PlannedSession
from coach.web.move_flow import adjacency_warnings, day_fr
from tests import test_web as tw
from tests.test_pilot import TODAY

# TODAY = mercredi 30/09/2026 ; plan d'affûtage : mer z2, jeu seuil, ven z1, sam z2, dim sortie longue,
# lun 5/10 z1, mar 6/10 repos, mer 7/10 tempo... course jeudi 15/10.


def profile(**kw) -> Profile:
    base = dict(goal_name="Grand Raid", goal_date=date(2026, 10, 15), goal_distance_km=165, goal_dplus_m=10000,
                events=[Event(name="Trail d'hiver", date=date(2026, 12, 6), role="B", distance_km=30),
                        Event(name="Semi de Nantes", date=date(2026, 11, 8), role="B", kind="route", distance_km=21.1),
                        Event(name="UTMB", year=2028, role="C"),
                        Event(name="Ultra 2027", date=date(2027, 6, 20), role="C", distance_km=100),
                        Event(name="Trail passé", date=date(2026, 9, 1), role="B", distance_km=20)])
    base.update(kw)
    return Profile(**base)


# --------------------------------------------------------------------------- objectifs
def test_goals_main_first_then_secondary_and_projects_by_date():
    g = goals_list(profile(), TODAY)
    assert [x["name"] for x in g] == ["Grand Raid", "Semi de Nantes", "Trail d'hiver", "Trail passé",
                                      "Ultra 2027", "UTMB"]
    assert [x["role"] for x in g] == ["A", "B", "B", "B", "C", "C"]
    main = g[0]
    assert main["label"] == "Objectif principal" and main["days_left"] == 15 and main["km"] == 165
    assert main["dplus"] == 10000 and main["date"] == "2026-10-15" and not main["done"]
    assert g[1]["label"] == "Objectif secondaire" and g[1]["kind"] == "route" and g[1]["days_left"] == 39
    assert g[3]["done"] and g[3]["days_left"] < 0                 # passé : en fin de groupe, « terminé »
    assert g[5]["date"] is None and g[5]["year"] == 2028           # date inconnue : en fin de groupe
    assert g[4]["label"] == "Projet"


def test_goals_without_profile_fall_back_to_settings():
    assert goals_list(None, TODAY) == []
    g = goals_list(None, TODAY, "Diagonale", date(2026, 10, 15))
    assert len(g) == 1 and g[0]["role"] == "A" and g[0]["days_left"] == 15


def test_state_and_progress_expose_sorted_goals(tmp_path):
    c = tw.make(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    c.pilot.store.set("profile", profile().model_dump(mode="json"))
    st = c.get("/api/state").json()
    assert [g["name"] for g in st["goals"]][:3] == ["Grand Raid", "Semi de Nantes", "Trail d'hiver"]
    assert c.get("/api/goals").json()["goals"] == st["goals"]
    pr = c.get("/api/progress").json()
    cards = pr["goals"]
    # cartes d'estimation : principal puis secondaires à venir, par date (le passé n'a pas d'estimation)
    assert [x["name"] for x in cards] == ["Grand Raid", "Semi de Nantes", "Trail d'hiver"]
    assert [x["role"] for x in cards] == ["A", "B", "B"]
    assert pr["my_goals"][0]["name"] == "Grand Raid"
    # l'estimation calculée par Progrès est reprise dans « Mes objectifs »
    est = {x["name"]: x["estimate"] for x in cards}
    st2 = c.get("/api/state").json()
    assert all(g["estimate"] == est.get(g["name"]) for g in st2["goals"] if g["role"] != "C" and not g["done"])


# --------------------------------------------------------------------------- déplacement
def planned(c):
    return {s.session_id: s for s in c.pilot.store.plan()}


def chat_texts(c):
    assert c.service.wait_coach(30)                 # v0.38 : la réaction du coach arrive en tâche de fond
    return [(m["role"], m["text"]) for m in c.get("/api/chat").json()["messages"]]


def ready(tmp_path, llm=None):
    from coach.sandbox import HeuristicLLM
    c = tw.make(tmp_path, llm=llm or HeuristicLLM(TODAY))
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    c.service.store.set("session_meta", {"coach-2026-10-01": {"title": "Seuil 3 × 10 min", "kind": "seuil"}})
    return c


def test_day_label():
    assert day_fr(date(2026, 11, 3)) == "mardi 3/11"


def test_allowed_move_updates_plan_calendar_and_chat(tmp_path):
    c = ready(tmp_path)
    r = c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-02"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["moved"] and b["from"] == "2026-10-01" and b["to"] == "2026-10-02"
    assert b["session"]["title"] == "Seuil 3 × 10 min" and b["session"]["day"] == "2026-10-02"   # titre gardé
    p = planned(c)
    assert p["coach-2026-10-01"].day == date(2026, 10, 2)          # même identifiant, nouveau jour
    assert p["coach-2026-10-01"].intensity == "threshold"
    # agenda Google mis à jour par le même adaptateur que le graphe
    assert any("coach-2026-10-01" in op for op in b["calendar_ops"])
    ev = [e for e in c.gsvc.coach_events()
          if e["extendedProperties"]["private"]["coach_session_id"] == "coach-2026-10-01"]
    assert len(ev) == 1 and ev[0]["start"]["dateTime"].startswith("2026-10-02")
    msgs = chat_texts(c)
    assert ("user", "J'ai déplacé « Seuil 3 × 10 min » de jeudi 1/10 à vendredi 2/10.") in msgs
    assert msgs[-1][0] == "coach" and "agenda" in msgs[-1][1]
    assert "[Déplacement" not in "".join(t for _, t in msgs)       # la précision au coach n'est pas affichée
    # la semaine affiche la séance au nouveau jour
    w = c.get("/api/week").json()
    fri = next(d for d in w["days"] if d["date"] == "2026-10-02")
    assert [s["session_id"] for s in fri["sessions"]][0] in ("coach-2026-10-01", "coach-2026-10-02")
    assert "coach-2026-10-01" in [s["session_id"] for s in fri["sessions"]]
    # deux séances le même jour : avertissement
    assert any("deux séances le vendredi 2/10" in x for x in b["warnings"])


def test_hard_sessions_back_to_back_warn_and_coach_mentions_it(tmp_path):
    c = ready(tmp_path)
    b = c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-03"}).json()   # seuil samedi, SL dimanche
    assert b["moved"]
    assert any("deux séances dures d'affilée" in w for w in b["warnings"])
    assert "dures d'affilée" in chat_texts(c)[-1][1]


def test_move_onto_rest_day_swaps_the_rest(tmp_path):
    c = ready(tmp_path)
    c.get("/api/chat")
    b = c.post("/api/sessions/coach-2026-10-07/move", json={"day": "2026-10-06"})     # tempo -> mardi (repos)
    # v0.38 : tempo ramené dans les 7 prochains jours (pas de rattrapage) : avertissement, plus de refus
    assert b.status_code == 200, b.text
    assert any("pas de rattrapage" in w and "aurait bloqué" in w for w in b.json()["warnings"])
    assert any(a.get("rule") == "R05_catch_up" and a["would_block"] for a in b.json()["alerts"])
    c.service.wait_coach()
    b = c.post("/api/sessions/coach-2026-10-08/move", json={"day": "2026-10-10"})     # z1 jeudi -> samedi (repos)
    assert b.status_code == 200, b.text
    p = planned(c)
    assert p["coach-2026-10-08"].day == date(2026, 10, 10)
    assert p["coach-2026-10-10"].day == date(2026, 10, 8) and p["coach-2026-10-10"].intensity == "rest"


def test_guardrail_violation_no_longer_blocks_the_move(tmp_path):
    """v0.38 : « je ne suis pas pour qu'on empêche de bouger une séance » : la violation devient un
    avertissement, transmis au coach, qui poste toujours un message."""
    c = ready(tmp_path)
    r = c.post("/api/sessions/coach-2026-10-07/move", json={"day": "2026-10-05"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["moved"] and planned(c)["coach-2026-10-07"].day == date(2026, 10, 5)
    assert any("aurait bloqué une proposition du coach" in w for w in b["warnings"])
    msgs = chat_texts(c)
    assert msgs[-2][0] == "user" and msgs[-2][1].startswith("J'ai déplacé")
    assert msgs[-1][0] == "coach" and "aurait bloqué" in msgs[-1][1]


def test_cannot_move_past_validated_race_or_rest_sessions(tmp_path):
    c = ready(tmp_path)
    plan = planned(c)
    # séance passée
    c.pilot.store.upsert_session(PlannedSession(session_id="old", day=TODAY - timedelta(days=2), intensity="z2",
                                                duration_min=40, planned_load=30))
    c.pilot.store.db.commit()
    r = c.post("/api/sessions/old/move", json={"day": "2026-10-02"})
    assert r.status_code == 200 and r.json()["moved"]                # v0.45 : séance manquée replanifiée
    assert c.post("/api/sessions/old/move", json={"day": "2026-09-27"}).status_code == 409   # jamais vers le passé
    # séance validée
    assert c.post("/api/sessions/coach-2026-09-30/feedback", json={"status": "done", "rpe": 4}).status_code == 200
    r = c.post("/api/sessions/coach-2026-09-30/move", json={"day": "2026-10-02"})
    assert r.status_code == 409 and "validée" in r.json()["error"]
    # course
    r = c.post("/api/sessions/coach-2026-10-15/move", json={"day": "2026-10-14"})
    assert r.status_code == 409 and "course" in r.json()["error"].lower()
    # repos
    r = c.post("/api/sessions/coach-2026-10-06/move", json={"day": "2026-10-05"})
    assert r.status_code == 409
    # vers le passé
    assert c.post("/api/sessions/coach-2026-10-02/move", json={"day": "2026-09-29"}).status_code == 409
    # inconnue, jour invalide
    assert c.post("/api/sessions/nope/move", json={"day": "2026-10-02"}).status_code == 404
    assert c.post("/api/sessions/coach-2026-10-02/move", json={"day": "demain"}).status_code == 422
    assert {k: v.day for k, v in planned(c).items() if k != "old"} == {k: v.day for k, v in plan.items()}
    # seul le déplacement autorisé (séance manquée « old ») est annoncé dans le chat
    assert [t for role, t in chat_texts(c) if t.startswith("J'ai déplacé")] == [
        "J'ai déplacé « Endurance Z2 » de lundi 28/9 à vendredi 2/10."]


def test_race_day_target_is_a_strong_warning_not_a_refusal(tmp_path):
    c = ready(tmp_path)
    r = c.post("/api/sessions/coach-2026-10-14/move", json={"day": "2026-10-15"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert any(a["level"] == "strong" and "jour de course" in a["text"] for a in b["alerts"])
    assert planned(c)["coach-2026-10-14"].day == date(2026, 10, 15)
    assert "jour de course" in chat_texts(c)[-1][1]


def test_same_day_is_idempotent(tmp_path):
    c = ready(tmp_path)
    n_chat = len(chat_texts(c))
    r = c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-01"})
    assert r.status_code == 200 and r.json()["moved"] is False and r.json()["messages"] == []
    assert len(chat_texts(c)) == n_chat
    c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-02"})
    r = c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-02"})   # rejouée : sans effet
    assert r.json()["moved"] is False
    assert sum(1 for role, t in chat_texts(c) if role == "user" and t.startswith("J'ai déplacé")) == 1


def test_move_goes_through_coach_pipeline_and_confirmation(tmp_path):
    """Avec un coach qui propose un changement majeur : la question oui/non suit le flux habituel ;
    v0.38 : un nouveau déplacement est fait quand même, la proposition reste en attente."""
    c = ready(tmp_path, llm=tw._major_llm())
    b = c.post("/api/sessions/coach-2026-10-03/move", json={"day": "2026-10-02"}).json()
    assert b["moved"]
    assert c.service.wait_coach()
    h = c.get("/api/chat").json()
    assert h["pending"] and "(oui / non)" in h["messages"][-1]["text"] and not h["typing"]
    r = c.post("/api/sessions/coach-2026-10-02/move", json={"day": "2026-10-03"})
    assert r.status_code == 200 and r.json()["pending"]
    last = r.json()["messages"][-1]["text"]
    assert "reste en attente" in last and "je garde ton déplacement" in last
    assert c.get("/api/chat").json()["pending"]                    # la question est toujours ouverte
    c.post("/api/chat", json={"text": "non"})
    assert c.post("/api/sessions/coach-2026-10-02/move", json={"day": "2026-10-04"}).status_code == 200


def test_baseline_follows_the_move(tmp_path):
    c = ready(tmp_path)
    base0 = dict(c.pilot.store.get("baseline_loads"))
    c.post("/api/sessions/coach-2026-10-01/move", json={"day": "2026-10-02"})
    base1 = c.pilot.store.get("baseline_loads")
    assert base1["2026-10-01"] == 0 and round(base1["2026-10-02"], 1) == round(base0["2026-10-02"] + base0["2026-10-01"], 1)


def test_adjacency_helper():
    def s(i, d, it="z2", race=False, load=40.0):
        return PlannedSession(session_id=i, day=d, intensity=it, duration_min=50, planned_load=load, is_race=race)
    d = date(2026, 10, 10)
    m = s("m", d, "vo2")
    plan = [m, s("a", d - timedelta(days=1), "long"), s("r", d + timedelta(days=2), "race", race=True, load=0)]
    w = adjacency_warnings(plan, m, lambda x: x.intensity)
    assert any("dures d'affilée" in x for x in w) and any("J-2 de la course" in x for x in w)
    assert adjacency_warnings([s("m", d), s("b", d + timedelta(days=1), "vo2")], s("m", d), lambda x: x.intensity) == []
