"""Tests unitaires de la plomberie (notifications, réglages, authentification, sauvegarde, dictée).

Ces modules touchent normalement l'environnement (os.environ), l'horloge, le réseau ou des fils d'exécution.
Ils reçoivent ici des remplaçants injectés (inversion de contrôle, voir docs/tests.md) : aucun appel réseau,
aucune attente réelle, aucune variable d'environnement modifiée.
"""
from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from coach import backup, checkpoint, stt, version
from coach.pilot import config
from coach.pilot.store import PilotStore
from coach.web import auth, push


# --------------------------------------------------------------------------- notifications Web Push
class FakeWebPush:
    """Remplace pywebpush.webpush : enregistre les envois, ou lève l'erreur demandée."""

    def __init__(self, status: int | None = None):
        self.calls, self.status = [], status

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.status:
            raise LibError(SimpleNamespace(status_code=self.status))


class LibError(Exception):
    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def test_pywebpush_sender_uses_injected_env_and_function(tmp_path):
    wp = FakeWebPush()
    send = push.pywebpush_sender(tmp_path, env={"COACH_PUSH_CONTACT": "mailto:moi@x.fr"}, webpush=wp, error=LibError)
    send({"endpoint": "e"}, '{"a": 1}')
    kw = wp.calls[0]
    assert kw["vapid_claims"] == {"sub": "mailto:moi@x.fr"} and kw["ttl"] == 12 * 3600
    assert kw["vapid_private_key"].endswith("vapid_private.pem")


@pytest.mark.parametrize("code,exc", [(410, push.PushGone), (404, push.PushGone), (500, LibError)])
def test_pywebpush_sender_maps_expired_subscriptions(tmp_path, code, exc):
    send = push.pywebpush_sender(tmp_path, env={}, webpush=FakeWebPush(code), error=LibError)
    with pytest.raises(exc):
        send({"endpoint": "e"}, "{}")


def test_pywebpush_sender_default_contact_and_real_library(tmp_path):
    wp = FakeWebPush()
    push.pywebpush_sender(tmp_path, env={}, webpush=wp)({"endpoint": "e"}, "{}")
    assert wp.calls[0]["vapid_claims"]["sub"] == "mailto:coach@localhost"
    assert callable(push.pywebpush_sender(tmp_path))          # la vraie bibliothèque est installée


def test_ensure_vapid_creates_keys_once(tmp_path):
    key = push.ensure_vapid(tmp_path)
    priv, pub = push.vapid_paths(tmp_path)
    assert key and len(key) > 80 and oct(priv.stat().st_mode)[-3:] == "600"
    assert push.ensure_vapid(tmp_path) == key == pub.read_text()


def test_notifier_counts_successes_and_drops_gone_subscriptions(tmp_path):
    store = PilotStore(tmp_path / "p.db")
    for e in ("ok", "gone", "broken"):
        store.add_push_sub({"endpoint": e, "keys": {}})
    sent = []

    def sender(sub, data):
        if sub["endpoint"] == "gone":
            raise push.PushGone("410")
        if sub["endpoint"] == "broken":
            raise RuntimeError("boom")
        sent.append(json.loads(data))

    n = push.Notifier(store, sender)
    assert n.enabled and n.send("Titre", "Corps", url="/#/week", tag="t") == 1
    assert sent == [{"title": "Titre", "body": "Corps", "url": "/#/week", "tag": "t"}]
    assert [s["endpoint"] for s in store.push_subs()] == ["ok", "broken"]
    off = push.Notifier(store, None)
    assert not off.enabled and off.send("a", "b") == 0


# --------------------------------------------------------------------------- réglages du pilote
def test_coach_home_from_injected_env(tmp_path):
    home = config.coach_home({"COACH_HOME": str(tmp_path / "h")})
    assert home == tmp_path / "h" and home.is_dir() and oct(home.stat().st_mode)[-3:] == "700"


def test_local_timezone_env_then_link_then_default(tmp_path):
    assert config.local_timezone({"COACH_TZ": "Indian/Reunion"}) == "Indian/Reunion"
    zone = tmp_path / "zoneinfo" / "Europe" / "Oslo"
    zone.parent.mkdir(parents=True)
    zone.write_text("")
    link = tmp_path / "localtime"
    link.symlink_to(zone)
    assert config.local_timezone({}, str(link)) == "Europe/Oslo"
    assert config.local_timezone({}, str(tmp_path / "absent")) == "Europe/Paris"


def test_load_settings_profile_then_env_overrides(tmp_path):
    (tmp_path / "data" / "athletes").mkdir(parents=True)
    (tmp_path / "data" / "athletes" / "pilote.json").write_text(json.dumps({"hr_rest": 45, "hr_max": 190}))
    env = {"COACH_TZ": "UTC", "COACH_LTHR": "170", "COACH_SESSION_HOUR": "7", "COACH_RACE_NAME": "VVX",
           "COACH_RACE_START": "2027-05-07T05:00:00+02:00", "COACH_GOAL": "finir", "COACH_HR_MAX": "195"}
    s = config.load_settings(env, repo_root=tmp_path)
    assert (s.hr_rest, s.hr_max, s.lthr, s.session_hour, s.tz) == (45.0, 195.0, 170.0, 7, "UTC")
    assert s.race_name == "VVX" and s.goal == "finir" and s.race_date.isoformat() == "2027-05-07"
    assert s.threshold_hr == 170
    d = config.load_settings({"COACH_TZ": "UTC"}, repo_root=tmp_path / "vide")
    assert d.hr_max == 199.0 and d.threshold_hr == round(0.93 * 199)


# --------------------------------------------------------------------------- authentification
class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_throttle_locks_after_failures_with_injected_clock():
    clock = Clock()
    th = auth.Throttle(max_fail=2, lock_s=60, clock=clock)
    th.record(False)
    assert th.allowed()
    th.record(False)
    assert not th.allowed()
    clock.t += 61
    assert th.allowed()
    th.record(False)
    th.record(True)                                   # un succès remet le compteur à zéro
    th.record(False)
    assert th.allowed()


def test_sign_verify_expiry_and_tampering():
    tok = auth.sign(b"k", "pilote", days=1, now=1000)
    assert auth.verify(b"k", tok, now=1000) == "pilote"
    assert auth.verify(b"k", tok, now=1000 + 2 * 86400) is None
    assert auth.verify(b"autre", tok, now=1000) is None
    assert auth.verify(b"k", None) is None and auth.verify(b"k", "sanspoint") is None
    body = auth._b64(b"v2|x|99999999999")
    import hashlib
    import hmac
    mac = auth._b64(hmac.new(b"k", body.encode(), hashlib.sha256).digest())
    assert auth.verify(b"k", f"{body}.{mac}") is None              # mauvaise version
    bad = auth._b64(b"pas de barres")
    mac = auth._b64(hmac.new(b"k", bad.encode(), hashlib.sha256).digest())
    assert auth.verify(b"k", f"{bad}.{mac}") is None               # corps illisible


def test_load_secret_env_or_private_file(tmp_path):
    assert auth.load_secret(tmp_path, {"COACH_WEB_SECRET": "abc"}) == b"abc"
    s1 = auth.load_secret(tmp_path, {})
    assert len(s1) > 40 and auth.load_secret(tmp_path, {}) == s1
    assert oct((tmp_path / "web_secret").stat().st_mode)[-3:] == "600"


def test_check_passcode():
    assert auth.check_passcode(" 1234 ", "1234") and not auth.check_passcode("1234", "")
    assert not auth.check_passcode("12345", "1234")


# --------------------------------------------------------------------------- sauvegarde
class FakePut:
    def __init__(self, status=200, exc=None):
        self.status, self.exc, self.calls = status, exc, []

    def __call__(self, url, content, headers, timeout):
        self.calls.append((url, len(content), headers["Content-Type"]))
        if self.exc:
            raise self.exc
        return SimpleNamespace(status_code=self.status)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "private").mkdir(parents=True)
    (home / "private" / "visage.jpg").write_bytes(b"\xff\xd8\xff")
    db = sqlite3.connect(home / "pilot.db")
    db.execute("create table t(x)")
    db.commit()
    db.close()
    return home


@pytest.mark.parametrize("put,url,code", [
    (FakePut(200), "https://os.example/p/", 0),
    (FakePut(403), "https://os.example/p", 2),
    (FakePut(exc=OSError("réseau")), "https://os.example/p", 2),
    (FakePut(200), "", 0),
])
def test_backup_main_local_and_remote(tmp_path, capsys, put, url, code):
    home = _home(tmp_path)
    assert backup.main([], env={"COACH_HOME": str(home), "COACH_BACKUP_URL": url}, http=put) == code
    archives = list((home / "backups").glob("coach-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as tar:
        assert "pilot.db" in tar.getnames() and not any(n.startswith("private") for n in tar.getnames())
    if url:
        assert put.calls[0][0] == "https://os.example/p/" + archives[0].name
    else:
        assert put.calls == [] and "non défini" in capsys.readouterr().out


def test_backup_is_sqlite_handles_unreadable(tmp_path):
    assert not backup._is_sqlite(tmp_path / "absent")


# --------------------------------------------------------------------------- version, checkpoints
def test_app_version_reads_file_or_unknown(monkeypatch, tmp_path):
    assert version.app_version()[0].isdigit()
    fake = tmp_path / "coach" / "version.py"
    fake.parent.mkdir()
    monkeypatch.setattr(version, "__file__", str(fake))
    assert version.app_version() == "inconnue"


def test_sqlite_saver_is_private(tmp_path):
    saver = checkpoint.sqlite_saver(tmp_path / "ck.db")
    assert saver is not None and oct((tmp_path / "ck.db").stat().st_mode)[-3:] == "600"


# --------------------------------------------------------------------------- dictée serveur
class FakeWhisper:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        return iter(self.segments), None


def seg(start, text):
    return SimpleNamespace(start=start, text=text)


def test_transcriber_env_settings_are_injected():
    t = stt.Transcriber(env={"COACH_STT": "off", "COACH_STT_MODEL": "base", "COACH_STT_DIR": "/m"})
    assert (t.enabled, t.model_name, t.root) == (False, "base", "/m") and not t.available()
    assert t.status() == {"server": False, "ready": False, "model": "base", "loading": False}


def test_transcriber_loads_model_through_factory_and_stops_after_three_minutes(tmp_path):
    made = []
    model = FakeWhisper([seg(0, " Demain "), seg(10, "je cours "), seg(stt.MAX_SECONDS + 1, "coupé")])

    def factory(name, root):
        made.append((name, root))
        return model

    spawned = []
    t = stt.Transcriber("tiny", tmp_path / "m", True, env={}, model_factory=factory, spawn=spawned.append)
    assert t.available()
    t.warm_up()                                       # le préchargement est confié à `spawn` (pas de fil réel)
    assert spawned == [t._load] and made == []
    spawned[0]()
    assert made == [("tiny", str(tmp_path / "m"))] and t.status()["ready"]
    t.warm_up()
    assert len(spawned) == 1                          # déjà chargé : rien à relancer
    assert t.transcribe(b"x" * 10, ".ogg") == "Demain je cours"
    assert model.calls[0][0].endswith(".ogg") and model.calls[0][1]["language"] == "fr"
    assert t._load() is model


def test_transcriber_errors(tmp_path):
    t = stt.Transcriber(enabled=True, env={}, model_factory=lambda n, r: FakeWhisper([]), root=tmp_path)
    with pytest.raises(ValueError):
        t.transcribe(b"")
    with pytest.raises(ValueError):
        t.transcribe(b"x" * (stt.MAX_BYTES + 1))

    def broken(name, root):
        raise OSError("pas de réseau")

    b = stt.Transcriber(enabled=True, env={}, model_factory=broken, root=tmp_path)
    with pytest.raises(RuntimeError):
        b.transcribe(b"x")
    assert not b.available() and "réseau" in b._error
    with pytest.raises(RuntimeError):
        b.transcribe(b"x")
    with pytest.raises(RuntimeError):
        stt.Transcriber(enabled=False, env={}).transcribe(b"x")


def test_transcriber_returns_none_model_is_unavailable(tmp_path):
    t = stt.Transcriber(enabled=True, env={}, model_factory=lambda n, r: None, root=tmp_path)
    with pytest.raises(RuntimeError):
        t.transcribe(b"x")


def test_transcriber_without_library(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_whisper(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_whisper)
    assert not stt.Transcriber(enabled=True, env={}).available()


def test_clean_and_suffix():
    assert stt.clean("  bonjour   coach ") == "bonjour coach"
    assert stt.clean("Séance de seuil, fractionné, VMA") == ""          # vocabulaire recopié sur un silence
    assert stt.suffix_for("audio/ogg; codecs=opus") == ".ogg" and stt.suffix_for(None) == ".webm"
