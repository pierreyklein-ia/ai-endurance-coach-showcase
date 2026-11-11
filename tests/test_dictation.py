"""v0.21 : dictée (transcription serveur, sans le vrai modèle Whisper)."""
import dataclasses
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coach import stt
from coach.pilot.runtime import build_pilot
from coach.web.app import create_app
from coach.web.push import Notifier
from coach.web.service import CoachService
from tests.fakes_google import Svc
from tests.test_pilot import ST, TODAY
from tests.test_web import CODE, SECRET, FakeSender


class FakeModel:
    def __init__(self, text=" Fais-moi une séance de seuil  demain ."):
        self.text, self.calls = text, []

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        return iter([SimpleNamespace(start=0.0, text=self.text)]), None


def client(tmp_path, transcriber, logged=True):
    p = build_pilot(home=tmp_path, calendar_service=Svc(), today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    app = create_app(CoachService(p, Notifier(p.store, FakeSender())), passcode=CODE, secret=SECRET,
                     background=False, stt=transcriber)
    c = TestClient(app)
    if logged:
        assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    return c


def ready(text=None):
    t = stt.Transcriber(enabled=True)
    t.available = lambda: True
    t._model = FakeModel(*([text] if text is not None else []))
    return t


def test_dictation_transcribes_in_french_with_running_vocabulary(tmp_path):
    t = ready()
    c = client(tmp_path, t)
    r = c.post("/api/dictation", content=b"x" * 5000, headers={"Content-Type": "audio/mp4"})
    assert r.status_code == 200 and r.json()["text"] == "Fais-moi une séance de seuil demain ."
    path, kw = t._model.calls[0]
    assert path.endswith(".mp4") and kw["language"] == "fr" and "Nordic curl" in kw["initial_prompt"]
    assert c.get("/api/dictation").json()["server"] is True


def test_dictation_protected_and_limited(tmp_path):
    t = ready()
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    assert client(tmp_path / "a", t, logged=False).post("/api/dictation", content=b"x").status_code == 401
    c = client(tmp_path / "b", t)
    assert c.post("/api/dictation", content=b"").status_code == 422
    big = b"x" * (stt.MAX_BYTES + 1)
    assert c.post("/api/dictation", content=big).status_code == 413


def test_dictation_disabled_is_503_and_reported(tmp_path):
    c = client(tmp_path, stt.Transcriber(enabled=False))
    assert c.get("/api/dictation").json()["server"] is False
    assert c.post("/api/dictation", content=b"x" * 5000).status_code == 503


def test_prompt_echo_on_silence_is_dropped(tmp_path):
    c = client(tmp_path, ready(text=" Séance de seuil, fractionné, VMA."))
    assert c.post("/api/dictation", content=b"x" * 5000).json()["text"] == ""


def test_headers_allow_microphone_for_the_app(tmp_path):
    c = client(tmp_path, ready(), logged=False)
    assert "microphone=(self)" in c.get("/").headers["permissions-policy"]


def test_suffix_for_browser_formats():
    assert stt.suffix_for("audio/webm;codecs=opus") == ".webm"
    assert stt.suffix_for("audio/mp4") == ".mp4"                  # iPhone (Safari)
    assert stt.suffix_for(None) == ".webm"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")
@pytest.mark.parametrize("ext,args", [(".webm", ["-c:a", "libopus"]), (".mp4", ["-c:a", "aac"])])
def test_browser_audio_formats_decode(tmp_path, ext, args):
    """Les formats enregistrés par Chrome (webm/opus) et Safari (mp4/aac) se décodent pour Whisper."""
    fw = pytest.importorskip("faster_whisper")
    f = tmp_path / f"a{ext}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    *args, str(f)], check=True)
    audio = fw.decode_audio(str(f))
    assert 1.8 * 16000 < len(audio) < 2.2 * 16000
