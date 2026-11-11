"""API web : chaque erreur du service devient un code HTTP lisible (404, 409, 413, 422, 503) ; tâche de fond.

Le vrai CoachService est utilisé ; seule la méthode visée est remplacée (monkeypatch) par une fonction qui lève.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from coach.web.app import create_app
from coach.web.move_flow import MoveRefused
from tests.test_web import CODE, SECRET, make


def raiser(exc):
    def f(*a, **k):
        raise exc
    return f


FEEDBACK = {"status": "done"}
NUTRI = {"items": [], "water_ml": 0, "digestion": 3}
CASES = [
    ("post", "/api/sessions/x/feedback", FEEDBACK, "feedback", KeyError("x"), 404),
    ("post", "/api/sessions/x/feedback", FEEDBACK, "feedback", ValueError("statut"), 422),
    ("post", "/api/sessions/x/move", {"day": "2026-10-01"}, "move_session", KeyError("x"), 404),
    ("post", "/api/sessions/x/move", {"day": "2026-10-01"}, "move_session", RuntimeError("agenda"), 503),
    ("post", "/api/nutrition", NUTRI, "save_nutrition", KeyError("s"), 404),
    ("post", "/api/nutrition", NUTRI, "save_nutrition", ValueError("v"), 422),
    ("get", "/api/race/sim", None, "race_sim", ValueError("pas de course"), 422),
    ("post", "/api/race/points", {"points": [{"km": 5}]}, "set_race_points", LookupError("course"), 404),
    ("post", "/api/race/points", {"points": [{"km": 5}]}, "set_race_points", ValueError("ordre"), 422),
    ("get", "/api/sessions/x/link", None, "link_candidates", KeyError("x"), 404),
    ("post", "/api/sessions/x/link", {"activity_start": "2026-09-29T06:00"}, "link_session", KeyError("x"), 404),
    ("post", "/api/sessions/x/link", {"activity_start": "2026-09-29T06:00"}, "link_session", ValueError("v"), 422),
    ("post", "/api/sessions/x/link", {"activity_start": "2026-09-29T06:00"}, "link_session", RuntimeError("r"), 503),
    ("delete", "/api/sessions/x/link", None, "unlink_session", KeyError("x"), 404),
    ("post", "/api/career/birthday", {"day": 31, "month": 2}, "set_birthday", ValueError("x"), 422),
    ("post", "/api/career/strength", {"exercise": "squat", "kg": 1, "bodyweight": 70}, "add_strength",
     ValueError("charge"), 422),
    ("post", "/api/onboarding/start", None, "start_conversation", ValueError("déjà"), 422),
]


@pytest.mark.parametrize("method,path,body,attr,exc,code", CASES)
def test_service_errors_map_to_http_codes(tmp_path, monkeypatch, method, path, body, attr, exc, code):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, attr, raiser(exc))
    kw = {"json": body} if body is not None else {}
    r = getattr(c, method)(path, **kw)
    assert r.status_code == code, r.text


def test_move_refused_is_409_with_rules(tmp_path, monkeypatch):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, "move_session", raiser(MoveRefused("trop de charge", ["R2"])))
    r = c.post("/api/sessions/x/move", json={"day": "2026-10-01"})
    assert r.status_code == 409 and r.json() == {"error": "trop de charge", "rules": ["R2"]}


@pytest.mark.parametrize("exc,code", [(LookupError("pas de course"), 404), (ValueError("GPX invalide"), 422)])
def test_race_gpx_errors(tmp_path, monkeypatch, exc, code):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, "upload_race_gpx", raiser(exc))
    assert c.post("/api/race/gpx", content=b"<gpx/>").status_code == code


def test_race_gpx_too_big_and_delete(tmp_path):
    from coach.web.race_flow import GPX_MAX
    c = make(tmp_path)
    assert c.post("/api/race/gpx", content=b"x", headers={"content-length": str(GPX_MAX + 1)}).status_code == 413
    assert c.delete("/api/race/gpx").status_code == 200


def test_upload_unreadable_file(tmp_path, monkeypatch):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, "upload", raiser(OSError("disque")))
    r = c.post("/api/upload?name=x.fit", content=b"x")
    assert r.status_code == 422 and "illisible" in r.json()["error"]


@pytest.mark.parametrize("exc,code", [(PermissionError("non"), 403), (RuntimeError("Gemini"), 503),
                                      (ValueError("pas une photo"), 422)])
def test_career_face_errors(tmp_path, monkeypatch, exc, code):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, "set_face", raiser(exc))
    assert c.post("/api/career/face", content=b"\xff\xd8\xff").status_code == code


def test_career_face_too_big_and_avatar_png(tmp_path, monkeypatch):
    from coach.avatar_image import FACE_MAX
    c = make(tmp_path)
    assert c.post("/api/career/face", content=b"x", headers={"content-length": str(FACE_MAX + 1)}).status_code == 413
    assert c.get("/api/career/avatar.png").status_code == 404
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(c.service, "avatar_png", lambda: png)
    r = c.get("/api/career/avatar.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_about_checks_use_service(tmp_path, monkeypatch):
    c = make(tmp_path)
    monkeypatch.setattr(c.service, "check_image_key", lambda: {"ok": False, "message": "aucune clé"})
    assert c.post("/api/about/image-check").json()["message"] == "aucune clé"


class BrokenStt:
    def __init__(self, exc):
        self.exc = exc

    def status(self):
        return {"server": True}

    def transcribe(self, data, suffix):
        raise self.exc

    def warm_up(self):
        pass


@pytest.mark.parametrize("exc,code", [(RuntimeError("off"), 503), (OSError("ffmpeg"), 422)])
def test_dictation_errors(tmp_path, exc, code):
    c0 = make(tmp_path)
    c = TestClient(create_app(c0.service, passcode=CODE, secret=SECRET, background=False, stt=BrokenStt(exc)))
    c.post("/api/login", json={"passcode": CODE})
    assert c.post("/api/dictation", content=b"x" * 100).status_code == code


def test_strava_connect_origin_and_errors(tmp_path, monkeypatch):
    c = make(tmp_path)
    seen = []
    monkeypatch.setattr(c.service, "connect_strava", lambda origin, force: seen.append((origin, force)) or {"ok": 1})
    c.post("/api/strava/connect", content=b"pas du json")
    c.post("/api/strava/connect", json={"origin": "http://testserver", "force": True})
    c.post("/api/strava/connect", json={"origin": "https://evil.example"})
    assert seen == [("http://testserver/", False), ("http://testserver", True), ("http://testserver/", False)]
    monkeypatch.setattr(c.service, "connect_strava", raiser(RuntimeError("client id")))
    r = c.post("/api/strava/connect", json={})
    assert r.json()["status"] == "error" and "client id" in r.json()["message"]


def test_strava_callback_internal_error_is_a_readable_page(tmp_path):
    c = make(tmp_path)

    class Connector:
        error = None

        def callback(self, *a):
            raise RuntimeError("<script>")

    c.service.connector = Connector()
    r = c.get("/strava/callback", params={"code": "x", "state": "y"})
    assert "interrompue" in r.text and "&lt;script>" in r.text and "<script>" not in r.text


def test_reset_needs_confirmation_and_push_without_key(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/reset", json={}).status_code == 422
    c2 = TestClient(create_app(c.service, passcode=CODE, secret=SECRET, background=False, public_key=None))
    c2.post("/api/login", json={"passcode": CODE})
    assert c2.post("/api/push/subscribe", json={"endpoint": "e", "keys": {}}).status_code == 503


class SpyStt(BrokenStt):
    def __init__(self):
        super().__init__(None)
        self.warmed = 0

    def warm_up(self):
        self.warmed += 1


def test_background_loop_ticks_survives_errors_and_stops(tmp_path, monkeypatch):
    c0 = make(tmp_path)
    ticks = []

    def tick():
        ticks.append(1)
        raise RuntimeError("tâche en panne")

    monkeypatch.setattr(c0.service, "tick", tick)
    stt = SpyStt()
    app = create_app(c0.service, passcode=CODE, secret=SECRET, background=True, tick_every_s=0.01, stt=stt)
    with TestClient(app) as c:
        deadline = time.time() + 5
        while len(ticks) < 2 and time.time() < deadline:
            c.get("/api/me")
            time.sleep(0.02)
    assert stt.warmed == 1 and len(ticks) >= 2
