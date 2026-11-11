"""Accueil d'un nouvel athlète : branches de la connexion Strava, de la première synchro et de la conversation."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from coach.pilot.runtime import build_pilot
from coach.web.app import create_app
from coach.web.service import CoachService
from tests.test_onboarding import CODE, SECRET, FakeConnector, FakeStrava, chat
from tests.test_v048_partner import ST, TODAY, club_runner


def client(tmp_path, acts=None, connector=True):
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    svc = CoachService(p, connector=FakeConnector() if connector else None,
                       strava_factory=lambda: FakeStrava(acts if acts is not None else club_runner()), background=False)
    c = TestClient(create_app(svc, passcode=CODE, secret=SECRET, background=False))
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.pilot, c.service = p, svc
    return c


def test_connect_without_connector_explains_setup(tmp_path):
    c = client(tmp_path, connector=False)
    r = c.post("/api/strava/connect").json()
    assert r["status"] == "error" and "install.sh" in r["message"]
    ob = c.get("/api/onboarding").json()
    assert ob["step"] == "connect" and ob["connect"] is None and not ob["strava"]


def test_onboarding_view_while_syncing(tmp_path):
    c = client(tmp_path)
    c.service.job = {"running": True, "message": "Lecture…"}
    ob = c.get("/api/onboarding").json()
    assert ob["step"] == "syncing" and ob["job"]["message"] == "Lecture…"
    c.service.start_initial_sync()                            # déjà en cours : rien de plus
    assert c.service.job["message"] == "Lecture…"


def test_already_connected_fetches_first_name_and_survives_errors(tmp_path):
    c = client(tmp_path)
    c.service.connector.status = "connected"

    class Strava(FakeStrava):
        def athlete(self):
            raise RuntimeError("401")

    c.pilot.strava = Strava(club_runner())
    assert c.service.connect_strava("http://x")["status"] == "connected"
    assert not c.pilot.store.get("athlete_first_name")

    class Named(FakeStrava):
        def athlete(self):
            return {"firstname": "Camille"}

    c.pilot.strava = Named(club_runner())
    c.service.connect_strava("http://x")
    assert c.pilot.store.get("athlete_first_name") == "Camille"


def test_first_sync_failure_and_crash_are_reported(tmp_path, monkeypatch):
    c = client(tmp_path)
    monkeypatch.setattr(c.service, "sync", lambda progress=None: {"ok": False, "message": "quota Strava"})
    c.service.start_initial_sync()
    assert c.service.job == {"running": False, "error": "quota Strava"}

    def boom(progress=None):
        raise KeyError("champ")

    monkeypatch.setattr(c.service, "sync", boom)
    c.service.start_initial_sync()
    assert "KeyError" in c.service.job["error"]


def test_start_conversation_needs_data(tmp_path):
    c = client(tmp_path, acts=[])
    with pytest.raises(ValueError, match="pas encore de données"):
        c.service.start_conversation()
    assert c.post("/api/onboarding/start").status_code == 422


def connected(tmp_path, acts=None):
    c = client(tmp_path, acts)
    assert c.post("/api/strava/connect").json()["status"] == "connected"
    assert c.get("/api/onboarding").json()["step"] == "chat"
    return c


def test_unknown_race_without_date_asks_for_it(tmp_path):
    c = connected(tmp_path)
    r = chat(c, "Le trail des Écureuils Bleus 30 km")
    assert "pas trouvé la date de Le trail des Écureuils Bleus" in r["messages"][-1]["text"]
    assert c.pilot.store.get("ob_state") == "confirm_goal"


def test_ambiguous_event_then_format_answer_then_skip_profile(tmp_path):
    c = connected(tmp_path)
    r = chat(c, "Volvic Volcanic Experience")
    assert "plusieurs formats" in r["messages"][-1]["text"] and len(r["quick"]) == 6
    r = chat(c, "L'Impluvium")
    assert "L'Impluvium 43-46 km : 45 km, vendredi 07/05/2027, départ 8 h" in r["messages"][-1]["text"]
    assert c.pilot.store.get("ob_formats") is None
    r = chat(c, "Oui, c'est ça")
    assert r["quick"] == ["Passe, valeurs par défaut"]
    r = chat(c, "passe")                                     # valeurs par défaut, plafond estimé d'après l'historique
    assert "valeurs par défaut" in r["messages"][-1]["text"] and "Ça me va" in r["quick"]
    assert c.pilot.store.get("ob_state") == "negotiate"
