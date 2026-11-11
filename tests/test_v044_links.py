"""v0.44 : déplacer le renfo ; lier / délier une séance et la sortie réellement faite ; le coach remarque l'écart."""
from datetime import timedelta

from coach.pilot.store import PROFILES_KEY
from coach.web.auto_validate import AUTO_KEY, UNLINKED_KEY
from tests.test_pilot import TODAY
from tests.test_v037 import ready
from tests.test_v042_auto import run, sess
from tests.test_v043_session_type import EASY, VMA

Y = TODAY - timedelta(days=1)


def chat(c):
    assert c.service.wait_coach(20)
    return c.get("/api/chat").json()["messages"]


def test_strength_session_can_be_moved(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("renfo-1", TODAY + timedelta(days=1), 40, "rest"))
    st.set("session_meta", {**(st.get("session_meta") or {}), "renfo-1": {"title": "Renfo jambes", "kind": "renfo"}})
    r = c.post("/api/sessions/renfo-1/move", json={"day": (TODAY + timedelta(days=2)).isoformat()})
    assert r.status_code == 200 and r.json()["moved"]
    assert next(s for s in st.plan() if s.session_id == "renfo-1").day == TODAY + timedelta(days=2)
    st.upsert_session(sess("repos-1", TODAY + timedelta(days=3), 0, "rest"))
    assert c.post("/api/sessions/repos-1/move", json={"day": (TODAY + timedelta(days=4)).isoformat()}).status_code == 409


def setup_yesterday(c):
    """Hier : seuil prévu (déplacé là par l'athlète), mais il a fait de la VMA ; demain : sprints en côte."""
    st = c.pilot.store
    st.upsert_session(sess("seuil", Y, 60, "threshold"))
    st.upsert_session(sess("cotes", TODAY + timedelta(days=1), 55, "hills"))
    st.set("session_meta", {**(st.get("session_meta") or {}), "seuil": {"title": "Seuil 3 × 10 min"},
                            "cotes": {"title": "Sprints en côte"}})
    a = run(Y, 61, name="Vhs")
    a.profile = VMA
    st.add_activity(a)
    return a


def test_mismatch_is_modified_and_the_coach_says_so(tmp_path):
    c = ready(tmp_path)
    setup_yesterday(c)
    done = c.service.auto_validate()
    got = next(d for d in done if d["session_id"] == "seuil")
    assert got["status"] == "modified" and got["type"]["label"] == "VMA"
    msgs = chat(c)
    line = next(m for m in msgs if m["role"] == "user" and "Validation automatique" in m["text"])
    assert "VMA au lieu de « Seuil 3 × 10 min »" in line["text"]
    assert msgs[-1]["role"] == "coach"
    assert c.service.auto_validate() == []                   # pas de second message


def test_unlink_then_link_to_the_hill_sprints(tmp_path):
    c = ready(tmp_path)
    a = setup_yesterday(c)
    c.service.auto_validate()
    # délier : le seuil redevient « à valider », et la sortie n'y est plus jamais remise automatiquement
    r = c.delete("/api/sessions/seuil/link")
    assert r.status_code == 200
    st = c.pilot.store
    assert "seuil" not in st.feedback_for(["seuil"]) and "seuil" not in (st.get(AUTO_KEY) or {})
    assert f"seuil|{a.start_utc.isoformat()}" in st.get(UNLINKED_KEY)
    assert all(not (d["session_id"] == "seuil" and d["start"] == a.start_utc.isoformat())
               for d in c.service.auto_validate())
    c.service.unlink_session("seuil") if "seuil" in (st.get(AUTO_KEY) or {}) else None   # autre sortie du jeu de test
    # candidats pour les sprints en côte (± 3 jours) : la sortie VMA d'hier, conforme à des côtes
    cand = c.get("/api/sessions/cotes/link").json()["candidates"]
    vma = next(x for x in cand if x["start"] == a.start_utc.isoformat())
    assert vma["type"]["verdict"] == "conforme" and vma["linked_to"] is None
    # lier : la séance de côtes rejoint le jour de la sortie, validée « faite »
    r = c.post("/api/sessions/cotes/link", json={"activity_start": a.start_utc.isoformat()})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["moved_from"] == (TODAY + timedelta(days=1)).isoformat() and b["day"] == Y.isoformat()
    assert b["feedback"]["status"] == "done" and b["feedback"]["auto"]["manual"]
    assert next(s for s in st.plan() if s.session_id == "cotes").day == Y
    assert any("J'ai lié ma sortie « Vhs »" in m["text"] for m in chat(c))


def test_linking_an_activity_already_linked_moves_it(tmp_path):
    c = ready(tmp_path)
    a = setup_yesterday(c)
    c.service.auto_validate()                                # la VMA valide le seuil
    c.post("/api/sessions/cotes/link", json={"activity_start": a.start_utc.isoformat()})
    st = c.pilot.store
    links = st.get(AUTO_KEY)
    assert "seuil" not in links and links["cotes"]["start"] == a.start_utc.isoformat()
    assert "seuil" not in st.feedback_for(["seuil"])         # retour automatique retiré avec le lien


def test_link_refuses_other_sport_or_far_days(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("x", Y, 50))
    far = run(Y - timedelta(days=5), 50)
    bike = run(Y, 50, sport="Vélo", km=30)
    st.add_activity(far)
    st.add_activity(bike)
    assert c.post("/api/sessions/x/link", json={"activity_start": bike.start_utc.isoformat()}).status_code == 422
    assert c.post("/api/sessions/x/link", json={"activity_start": far.start_utc.isoformat()}).status_code == 422
    assert c.delete("/api/sessions/x/link").status_code == 404


def test_feedback_keeps_the_link(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("easy", Y, 50))
    a = run(Y, 50)
    a.profile = EASY
    st.add_activity(a)
    assert st.get(PROFILES_KEY)
    c.service.auto_validate()
    c.post("/api/sessions/easy/feedback", json={"status": "done", "rpe": 4, "note": ""})
    info = st.get(AUTO_KEY)["easy"]
    assert info["confirmed"] and info["start"] == a.start_utc.isoformat()


def test_missed_session_can_be_rescheduled_and_coach_is_told(tmp_path):
    """v0.45 : seuil prévu hier, pas fait : on le replanifie à demain."""
    c = ready(tmp_path)
    c.pilot.store.upsert_session(sess("seuil-rate", Y, 60, "threshold"))
    r = c.post("/api/sessions/seuil-rate/move", json={"day": (TODAY + timedelta(days=1)).isoformat()})
    assert r.status_code == 200 and r.json()["moved"]
    assert next(s for s in c.pilot.store.plan() if s.session_id == "seuil-rate").day == TODAY + timedelta(days=1)


def test_strava_rename_after_sync_updates_name_and_link_view(tmp_path):
    from coach.pilot.strava_api import refresh_recent_names
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("easy2", Y, 50))
    a = run(Y, 50, name="Evening Run")
    st.add_activity(a)
    c.service.auto_validate()

    class Api:
        def get(self, path, params=None):
            return [{"start_date": a.start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "name": "10×300 m"}]
    assert refresh_recent_names(st, Api(), TODAY) == 1
    assert refresh_recent_names(st, Api(), TODAY) == 0         # déjà à jour
    w = c.get(f"/api/week?start={Y - timedelta(days=Y.weekday())}").json()
    s = next(x for d in w["days"] for x in d["sessions"] if x["session_id"] == "easy2")
    assert s["feedback"]["auto"]["activity"] == "10×300 m"
