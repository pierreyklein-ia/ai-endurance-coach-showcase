"""Recherche de course (client Claude simulé), images Gemini (client HTTP simulé), trace de l'équipe d'agents."""
from __future__ import annotations

import base64
from datetime import date
from types import SimpleNamespace as NS

import pytest

from coach import avatar_image as av
from coach import race_lookup as rl
from coach.agents import trace

TODAY = date(2026, 11, 1)


# --------------------------------------------------------------------------- recherche de course
class FakeClaude:
    """Client Anthropic simulé : renvoie le texte donné (ou lève)."""

    def __init__(self, text=None, exc=None, searches=1):
        self.text, self.exc, self.searches, self.calls = text, exc, searches, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return NS(content=[NS(type="text", text=self.text), NS(type="tool_use")],
                  usage=NS(input_tokens=2000, output_tokens=200,
                           server_tool_use=NS(web_search_requests=self.searches)))


JS = '{"found": true, "name": "Marathon de Nantes", "date": "%s", "distance_km": 42.2, "kind": "route", ' \
     '"url": "https://www.marathon-nantes.fr/inscription"}'


def test_web_search_parses_json_and_costs():
    c = FakeClaude("Voici : " + JS % "2027-04-25")
    f = rl.web_search(c, "m", "Marathon de Nantes", 42.2, TODAY)
    assert f.date == date(2027, 4, 25) and f.kind == "route" and f.source == "marathon-nantes.fr"
    assert f.cost_eur == pytest.approx((2000 * 3 + 200 * 15) / 1e6 * 0.86 + rl.WEB_SEARCH_EUR, abs=1e-4)
    assert "format 42.2 km" in c.calls[0]["messages"][0]["content"]
    assert rl.web_search(FakeClaude(JS % "2028-04-23"), "m", "x", None, TODAY, 2028).date.year == 2028


@pytest.mark.parametrize("text,year", [
    ("pas de JSON", None),
    ('{"found": false}', None),
    (JS % "2026-04-25", None),                    # déjà passée
    (JS % "2027-04-25", 2028),                    # mauvaise année
])
def test_web_search_rejections(text, year):
    assert rl.web_search(FakeClaude(text), "m", "Marathon", None, TODAY, year) is None


def test_lookup_uses_web_for_unknown_race_and_survives_errors(monkeypatch):
    llm = NS(client=FakeClaude(JS % "2027-04-25"))
    assert rl.lookup("Marathon de Nantes", None, TODAY, llm).name == "Marathon de Nantes"
    assert rl.lookup("Marathon de Nantes", None, TODAY, NS(client=FakeClaude(exc=RuntimeError("off")))) is None
    assert rl.lookup("Marathon de Nantes", None, TODAY, None) is None
    monkeypatch.setattr(rl, "from_catalog", lambda *a: (_ for _ in ()).throw(ValueError("catalogue cassé")))
    assert rl.lookup("VVX", None, TODAY) is None


def test_catalog_formats_options_and_missing_catalog(monkeypatch, tmp_path):
    evs = rl.catalog_event("VVX", TODAY)
    assert len(evs) == 6
    f = rl.lookup("Volvic Volcanic Experience", None, TODAY)
    assert f.ambiguous and "plusieurs formats" in rl.options_text(f) and "départ" in rl.options_text(f)
    assert rl.from_catalog("trail des vallées", None, TODAY).distance_km == 104
    assert rl.from_catalog("trail patrimoine", 80, TODAY) is None
    assert rl.stated_distance("VVX 21,5 km") == 21.5 and rl.stated_distance("VVX") is None
    monkeypatch.setattr(rl, "CATALOG", tmp_path / "absent.json")
    assert rl.from_catalog("VVX", None, TODAY) is None and rl.catalog_event("VVX", TODAY) == []


def test_cross_check_keeps_web_result_when_not_in_catalog():
    f = rl.Found("Marathon", date(2027, 4, 25), 42.2)
    assert rl._cross_check(f, "Marathon de Nantes", 42.2, TODAY, None) is f


# --------------------------------------------------------------------------- images Gemini
class Resp:
    def __init__(self, status=200, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text

    def json(self):
        if self._js is None:
            raise ValueError("pas du JSON")
        return self._js


def png_js(data=b"PNG"):
    return {"candidates": [{"content": {"parts": [{"text": "ok"}, {"inlineData": {"data": base64.b64encode(data).decode()}}]}}]}


class FakeHttp:
    def __init__(self, *responses, exc=None):
        self.responses, self.exc, self.posts, self.gets = list(responses), exc, [], []

    def post(self, url, headers, json):
        self.posts.append(json)
        return self.responses.pop(0)

    def get(self, url, headers):
        self.gets.append(url)
        if self.exc:
            raise self.exc
        return self.responses.pop(0)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("COACH_IMAGE_MODEL", raising=False)


def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not av.enabled()
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert av.enabled()
    monkeypatch.setenv("COACH_IMAGE_PER_DAY", "x")
    monkeypatch.setenv("COACH_IMAGE_EVERY_H", "y")
    assert av.per_day() == 4 and av.every_hours() == 48
    monkeypatch.setenv("COACH_IMAGE_PER_DAY", "-3")
    monkeypatch.setenv("COACH_IMAGE_EVERY_H", "12")
    monkeypatch.setenv("COACH_IMAGE_MODEL", "m2")
    assert av.per_day() == 0 and av.every_hours() == 12 and av.model() == "m2"


def test_generate_with_reference_and_face(key):
    http = FakeHttp(Resp(200, png_js(b"IMG")))
    assert av.generate("Un coureur", reference=b"REF", client=http, face=(b"FACE", "image/jpeg")) == b"IMG"
    parts = http.posts[0]["contents"][0]["parts"]
    assert len(parts) == 3 and "reference image" in parts[0]["text"] and "photo of the real runner" in parts[0]["text"]
    assert parts[2]["inline_data"]["mime_type"] == "image/jpeg"


def test_generate_without_key_and_after_all_variants_fail(key, monkeypatch):
    http = FakeHttp(*[Resp(400, {"error": {"message": "bad field", "status": "INVALID_ARGUMENT"}})] * 3)
    with pytest.raises(av.GeminiError, match="bad field"):
        av.generate("x", client=http)
    assert len(http.posts) == 3 and "imageConfig" not in http.posts[2]["generationConfig"]
    http = FakeHttp(Resp(400, {"error": {"message": "User location is not supported"}}))
    with pytest.raises(av.GeminiError, match="location"):
        av.generate("x", client=http)
    assert len(http.posts) == 1
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(av.GeminiError, match="absente"):
        av.generate("x", client=FakeHttp())
    assert av.check_key(FakeHttp())[0] is False


def test_check_key_network_error_and_messages(key):
    ok, msg = av.check_key(FakeHttp(exc=OSError("dns")))
    assert not ok and "injoignable" in msg
    ok, msg = av.check_key(FakeHttp(Resp(200, {})))
    assert ok and "trouvé" in msg
    ok, msg = av.check_key(FakeHttp(Resp(500, None, "Internal")))
    assert not ok and msg == "HTTP 500 : Internal"
    assert av.api_message(Resp(403, {"error": {}}, "Forbidden")) == "HTTP 403 : Forbidden"


def test_extract_image_reports_block_reason():
    with pytest.raises(RuntimeError, match="SAFETY"):
        av.extract_image({"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(RuntimeError, match="STOP"):
        av.extract_image({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "non"}]}}]})
    assert av.extract_image({"candidates": [{"content": {"parts": [{"inline_data": {"data": "QUJD"}}]}}]}) == b"ABC"


def test_save_keeps_last_six(tmp_path):
    import os
    for i in range(8):
        p = av.save(tmp_path, f"k{i}", b"x")
        os.utime(p, (i, i))
    assert len(list((tmp_path / "avatars").glob("avatar-*.png"))) == 6


def test_prompt_background_and_face_mime():
    p = av.prompt({"hair": "rase", "beard": True, "skin": 99}, 25, ["bandeau", "inconnu"], "collines", "route", "neige")
    assert "shaved head" in p and "Short beard" in p and "scenic road along the coast" in p and "5 small golden stars" in p
    assert "No accessories" in av.prompt({}, 1, [], None, None)
    assert av.background("hiver") == av.background("route", "hiver")             # ancien décor unique
    assert "sea of clouds" in av.background("sommet")
    assert "lava" in av.background("volcan", "nuit")
    assert av.face_mime(b"RIFF1234WEBPxx") == "image/webp" and av.face_mime(b"RIFF1234AVI ") is None
    assert av.face_mime(b"\x00\x00\x00\x18ftypheic") == "image/heic" and av.face_mime(b"GIF89a") is None
    assert av.key_for({}, 1, [], "route", None, ambiance="neige") != av.key_for({}, 1, [], "route", None)


# --------------------------------------------------------------------------- trace de l'équipe
def test_team_meta_line_and_labels():
    from coach.llm import Usage
    usages = [Usage("supervisor", "main", 1000, 100), Usage("spec_nutrition", "small", 500, 50)]
    note = {"name": "nutrition", "label": "Nutrition", "risk_level": "low", "veto": False, "evidence_ids": [],
            "tools": [{"name": "a"}, {"name": "b"}], "risks": ["r1", "r2", "r3", "r4"], "degraded": True}
    veto = {"name": "prevention", "label": "Prévention", "risk_level": "high", "veto": True, "evidence_ids": ["R1"],
            "tools": [{"name": "a"}]}
    tr = [("route", {"routing": {"specialists": ["nutrition"], "reason": "x", "by": "règles", "ms": 3}}),
          ("specialist", {"notes": [veto, note]}),
          ("critic", {"critic": {"verdict": "revise", "action": "revise"}}),
          ("critic", {"critic": {"verdict": "ok", "reasons": ["a"], "rounds": 2}})]
    meta = trace.team_meta(tr, usages)
    by_name = {a["name"]: a for a in meta["agents"]}
    assert set(by_name) == {"nutrition", "prevention"} and by_name["nutrition"]["degraded"]
    assert len(by_name["nutrition"]["risks"]) == 3 and by_name["nutrition"]["cost_eur"] > 0
    assert set(meta["cost_by_agent"]) == {"superviseur", "nutrition"}
    assert meta["critic"]["revised"] and meta["critic"]["rounds"] == 2
    line = trace.team_line(meta)
    assert "Nutrition (2 outils)" in line and "Prévention (1 outil) ⛔ veto" in line and "validé après 1 révision" in line
    assert trace.team_meta([("specialist", {})], []) == {}
    assert trace.team_line({}) == "" and trace.team_line({"routing": {}, "agents": []}) == ""
    assert trace.team_line({"routing": {"x": 1}, "agents": []}) == "Équipe consultée : coach seul"
    assert trace.critic_label(None) == "" and trace.critic_label({"verdict": "ok"}) == "validé"
    assert trace.critic_label({"verdict": "revise", "revised": True}) == "réserves transmises aux garde-fous"
    assert trace.critic_label({"verdict": "revise"}) == "à revoir"
    assert trace.agent_of("eye_charge") == "charge" and trace.agent_of("inconnu") == "inconnu"
