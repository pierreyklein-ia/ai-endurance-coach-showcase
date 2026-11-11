"""v0.28.1 : erreurs Gemini lisibles, variantes de requête, test de clé sans image."""
import base64

import pytest

from coach import avatar_image as ai


class Resp:
    def __init__(self, status, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text

    def json(self):
        if self._js is None:
            raise ValueError
        return self._js


class Client:
    def __init__(self, posts=(), get=None):
        self.posts, self.get_resp, self.bodies = list(posts), get, []

    def post(self, url, headers=None, json=None):
        self.bodies.append(json)
        return self.posts.pop(0)

    def get(self, url, headers=None):
        return self.get_resp


def err(status, msg, st="INVALID_ARGUMENT"):
    return Resp(status, {"error": {"code": status, "message": msg, "status": st}})


OK = Resp(200, {"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"PNG").decode()}}]}}]})


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")


def test_400_retries_with_other_request_shapes_then_succeeds():
    c = Client([err(400, "Invalid imageConfig"), err(400, "Only IMAGE not supported"), OK])
    assert ai.generate("x", client=c) == b"PNG"
    mods = [b["generationConfig"]["responseModalities"] for b in c.bodies]
    assert mods == [["IMAGE"], ["IMAGE"], ["TEXT", "IMAGE"]] and "imageConfig" not in c.bodies[1]["generationConfig"]


def test_invalid_key_stops_at_once_and_shows_googles_message():
    c = Client([err(400, "API key not valid. Please pass a valid API key.")])
    with pytest.raises(ai.GeminiError) as e:
        ai.generate("x", client=c)
    assert "API key not valid" in str(e.value) and len(c.bodies) == 1


def test_other_errors_are_readable():
    with pytest.raises(ai.GeminiError) as e:
        ai.generate("x", client=Client([err(429, "Quota exceeded", "RESOURCE_EXHAUSTED")]))
    assert "429" in str(e.value) and "Quota exceeded" in str(e.value)


def test_check_key_does_not_generate():
    assert ai.check_key(Client(get=Resp(200, {"name": "models/x"})))[0] is True
    ok, msg = ai.check_key(Client(get=err(404, "models/foo is not found", "NOT_FOUND")))
    assert not ok and "not found" in msg
