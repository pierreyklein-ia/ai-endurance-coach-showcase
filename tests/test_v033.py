"""v0.33 : trophées d'événements (Halloween, Noël, Nouvel An, anniversaire…), accessoires de saison."""
from datetime import date

from coach.career import Facts, career, easter, in_season, worn_gear
from tests.test_career import act
from tests.test_v025 import long_runner, make


def test_easter_dates():
    assert easter(2026) == date(2026, 4, 5) and easter(2027) == date(2027, 3, 28) and easter(2025) == date(2025, 4, 20)


def ids(c):
    return {t["id"]: t for t in c["trophies"]}


def test_event_trophies_from_history():
    acts = [act(date(2025, 10, 31), 8, 45), act(date(2025, 12, 25), 10, 55), act(date(2026, 1, 1), 5, 28),
            act(date(2026, 4, 6), 6, 33)] + [act(date(2025, 12, d), 6, 33) for d in range(1, 13)]
    c = career(Facts(acts, today=date(2026, 11, 2), birthday="04-06"))
    t = ids(c)
    assert t["halloween"]["day"] == "2025-10-31" and t["noel"]["day"] == "2025-12-25"
    assert t["nouvel_an"]["day"] == "2026-01-01" and t["paques"]["day"] == "2026-04-06"     # lundi de Pâques
    assert t["avent"]["day"] == "2025-12-12" and t["anniversaire"]["day"] == "2026-04-06"
    assert t["saint_sylvestre"]["day"] is None and "progress" not in t["halloween"]          # gagné : pas de progression
    assert c["families"]["evenements"] == "Événements"


def test_birthday_missing_shows_hint():
    t = ids(career(Facts([act(date(2026, 3, 3), 5, 28)], today=date(2026, 11, 2))))
    assert t["anniversaire"]["day"] is None and "non renseignée" in t["anniversaire"]["progress"]


def test_upcoming_events_and_advent_progress():
    acts = [act(date(2026, 12, d), 6, 33) for d in range(1, 6)]
    c = career(Facts(acts, today=date(2026, 12, 5)))
    ev = {e["id"]: e for e in c["events"]}
    assert ev["avent"]["live"] and ev["avent"]["count"] == 5 and ev["avent"]["need"] == 12 and not ev["avent"]["done"]
    assert "noel" in ev and ev["noel"]["days_left"] == 19 or "noel" not in ev        # au-delà de 14 jours : pas listé
    c2 = career(Facts([], today=date(2026, 10, 25)))
    assert any(e["id"] == "halloween" and e["days_left"] == 6 for e in c2["events"])


def test_seasonal_hats():
    assert in_season("bonnet_noel", date(2026, 12, 10)) and not in_season("bonnet_noel", date(2026, 7, 1))
    assert in_season("chapeau_fete", date(2026, 4, 8), "04-06") and not in_season("chapeau_fete", date(2026, 5, 1), "04-06")
    gear = [{"id": "casquette", "unlocked": True}, {"id": "montre", "unlocked": True},
            {"id": "bonnet_noel", "unlocked": True, "seasonal": True, "in_season": True},
            {"id": "chapeau_sorciere", "unlocked": True, "seasonal": True, "in_season": False}]
    assert worn_gear({"gear": None}, gear) == ["montre", "bonnet_noel"]           # le bonnet remplace la casquette
    assert worn_gear({"gear": ["casquette", "chapeau_sorciere"]}, gear) == ["chapeau_sorciere"]


def test_birthday_endpoint(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    r = c.post("/api/career/birthday", json={"day": 6, "month": 4}).json()
    assert r["birthday"] == "04-06"
    assert c.post("/api/career/birthday", json={"day": 31, "month": 2}).status_code == 422
    assert c.post("/api/career/birthday", json={}).json()["birthday"] is None


# --- v0.34 : photo du visage (niveau 10), décors de saison, sac d'ultra au niveau 20 -------------------------------
def test_face_photo_needs_level_10_and_gemini(tmp_path, monkeypatch):
    import coach.web.career_flow as cf
    from coach import avatar_image as ai
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    jpg = b"\xff\xd8\xff\xe0" + b"x" * 2000
    lvl = c.get("/api/career").json()["level"]
    r = c.post("/api/career/face", content=jpg, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == (403 if lvl < 10 else 503)                  # niveau trop bas, ou pas de clé Gemini
    monkeypatch.setattr("coach.career.FACE_LEVEL", 1)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []
    monkeypatch.setattr(ai, "generate", lambda text, ref=None, face=None: calls.append(face) or b"PNG")
    assert c.post("/api/career/face", content=b"pas une image", headers={"Content-Type": "image/jpeg"}).status_code == 422
    r = c.post("/api/career/face", content=jpg, headers={"Content-Type": "image/jpeg"}).json()
    assert r["avatar_image"]["face"] and calls and calls[-1] == (jpg, "image/jpeg")
    face_file = c.service._face_path()
    assert face_file.exists() and oct(face_file.stat().st_mode)[-3:] == "600"
    import tarfile

    from coach.backup import snapshot
    arc = snapshot(face_file.parent.parent, tmp_path / "bk")
    with tarfile.open(arc) as t:
        assert not any("private" in n for n in t.getnames())               # jamais dans les sauvegardes
    assert not c.delete("/api/career/face").json()["avatar_image"]["face"] and not face_file.exists()
    assert cf is not None


def test_face_mime_detection():
    from coach.avatar_image import face_mime
    assert face_mime(b"\x89PNG\r\n") == "image/png" and face_mime(b"RIFF1234WEBPxx") == "image/webp"
    assert face_mime(b"\x00\x00\x00\x18ftypheic") == "image/heic" and face_mime(b"GIF89a") is None


def test_scenes_and_gear_spread_to_level_20():
    from coach.career import GEAR, LEVEL_SCENES
    lv = {i: l for l, i, _ in GEAR}
    assert lv["frontale"] == 10 and lv["camelbak"] == 20
    sc = {i for _, i in LEVEL_SCENES}
    assert {"printemps", "ete", "automne", "hiver", "bord_de_mer", "montagne", "collines", "route"} <= sc
    c = career(Facts([act(date(2026, 3, 3), 5, 28)], today=date(2026, 11, 2)))
    assert c["scene"] == "route"                                           # niveau 1 : la saison n'est pas débloquée
