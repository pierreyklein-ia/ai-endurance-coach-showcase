"""Bots Telegram (test et pilote) : commandes, erreurs, synchro périodique, boucle de long polling.

Horloge (`clock`) et pause (`sleep`) sont injectées : la boucle infinie s'arrête quand le faux client lève
`Stop` (BaseException, donc non attrapée par les `except Exception` de la boucle).
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from coach.adapters.telegram_bot import CoachBot, TelegramAPI, esc
from coach.pilot.bot import PilotBot
from coach.pilot.runtime import build_pilot
from tests.fakes_google import Svc
from tests.test_pilot import ST, TODAY, fill_history

ME, STRANGER = 111, 999


class Stop(BaseException):
    """Arrête la boucle run_forever (n'hérite pas d'Exception)."""


class FakeHTTP:
    """Faux client HTTP Telegram : enregistre les messages, déroule un script de getUpdates."""

    def __init__(self, updates=None, files=None):
        self.sent, self.files, self.updates = [], files or {}, list(updates or [])

    def __call__(self, url, params, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {"ok": True, "result": {}}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": params["file_id"]}}
        if method == "getUpdates":
            if not self.updates:
                raise Stop
            step = self.updates.pop(0)
            if isinstance(step, Exception):
                raise step
            return {"ok": True, "result": step}
        return {"ok": False, "description": "méthode inconnue"}

    def get(self, url, timeout):
        return self.files[url.rsplit("/", 1)[-1]]

    def texts(self):
        return [t for _, t in self.sent]


def upd(text=None, chat=ME, uid=1, **extra):
    m = {"chat": {"id": chat}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": uid, "message": m}


# --------------------------------------------------------------------------- API Telegram
def test_api_call_error_send_split_and_download():
    http = FakeHTTP(files={"doc1": b"FIT"})
    api = TelegramAPI("T", http, http.get)
    with pytest.raises(RuntimeError, match="méthode inconnue"):
        api.call("inconnue")
    api.send(ME, "x" * 8500)
    assert [len(t) for t in http.texts()] == [4000, 4000, 500]
    assert api.download("doc1") == b"FIT"
    assert esc("<b>&") == "&lt;b&gt;&amp;"


# --------------------------------------------------------------------------- bot de test
def test_coach_bot_commands_and_edge_cases():
    http = FakeHTTP()
    b = CoachBot(TelegramAPI("T", http), {ME}, today=date(2026, 9, 30))
    b.handle_update({"update_id": 1})                         # ni message ni message modifié
    b.handle_update(upd(""))                                  # texte vide : rien
    assert http.sent == []
    b.handle_update(upd("/agenda"))
    b.handle_update(upd("/reset"))
    assert "réinitialisés" in http.texts()[-1] and b.version[ME] == 1
    b.handle_update(upd("/debug"))
    assert "activée" in http.texts()[-1]
    b.handle_update({"update_id": 2, "edited_message": {"chat": {"id": ME}, "text": "Je suis un peu fatigué"}})
    assert "<i>" in http.texts()[-1]                          # trace technique jointe en mode debug
    b.handle_update(upd("/debug"))
    assert "désactivée" in http.texts()[-1]


def test_coach_bot_run_forever_retries_and_reports_errors():
    sleeps = []
    http = FakeHTTP(updates=[OSError("réseau coupé"), [upd("/agenda", uid=5), upd("/start", uid=6)]])
    b = CoachBot(TelegramAPI("T", http), {ME}, today=date(2026, 9, 30), sleep=sleeps.append)
    calls = []
    real = b.handle_update

    def flaky(u):
        calls.append(u["update_id"])
        if u["update_id"] == 5:
            raise ValueError("boom")
        return real(u)

    b.handle_update = flaky
    with pytest.raises(Stop):
        b.run_forever()
    assert sleeps == [3] and calls == [5, 6]
    assert "Erreur interne : ValueError" in http.texts()[0] and "<pre>" in http.texts()[1]


# --------------------------------------------------------------------------- bot pilote
class FakeStravaMCP:
    """Faux connecteur MCP : une page d'activités, puis les flux FC demandés."""

    def __init__(self, items=None, fail=None, stream_error=None):
        self.items, self.fail, self.stream_error = items or [], fail, stream_error

    def call(self, name, args):
        if self.fail:
            raise self.fail
        return {"activities": self.items, "has_next_page": False}

    def batch(self, calls):
        return [self.stream_error or {} for _ in calls]


def item(i, day, minutes=50):
    return {"id": i, "start_local": f"{day.isoformat()}T07:00:00", "name": f"Sortie {i}", "sport_type": "Run",
            "summary": {"elapsed_time": minutes * 60, "moving_time": minutes * 60, "distance": minutes * 180,
                        "elevation_gain": 50, "average_heartrate": 140}}


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def pilot(tmp_path):
    p = build_pilot(home=tmp_path, calendar_service=Svc(), today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    return p


def make(pilot, **kw):
    http = FakeHTTP(kw.pop("updates", None), kw.pop("files", None))
    clock = kw.pop("clock", Clock())
    sleeps: list = []
    bot = PilotBot(TelegramAPI("T", http, http.get), {ME}, pilot, clock=clock, sleep=sleeps.append, **kw)
    return bot, http, sleeps


def test_pilot_sync_without_strava_and_with_errors(pilot):
    bot, http, _ = make(pilot)
    assert "pas connecté" in bot._sync(ME) and bot._sync(None, quiet=True) is None
    inner = RuntimeError("quota atteint")
    group = ExceptionGroup("anyio", [inner])
    pilot.strava = FakeStravaMCP(fail=group)
    assert "quota atteint" in bot._sync(ME)                      # ExceptionGroup déballé
    assert bot.last_sync == 10_000.0
    assert bot._sync(None, quiet=True) is None


def test_pilot_sync_reports_new_and_partial(pilot):
    bot, http, _ = make(pilot)
    pilot.strava = FakeStravaMCP([item(1, TODAY - timedelta(days=1)), item(2, TODAY - timedelta(days=2))])
    txt = bot._sync(ME)
    assert "2 nouvelle(s)" in txt and "Course 50 min" in txt
    assert bot._sync(None, quiet=True) is None                   # rien de neuf, silencieux
    pilot.store.set("strava_last_start_local", None)
    pilot.store.db.execute("DELETE FROM activities")
    pilot.strava = FakeStravaMCP([item(3, TODAY - timedelta(days=1))], stream_error=RuntimeError("429"))
    assert "partiel" in bot._sync(ME)


def test_pilot_tick_respects_period_and_broadcasts(pilot):
    clock = Clock()
    bot, http, _ = make(pilot, clock=clock, sync_every_min=60)
    bot.tick()                                                   # pas de Strava : rien
    pilot.strava = FakeStravaMCP([item(1, TODAY - timedelta(days=1))])
    bot.last_sync = clock.t - 30
    bot.tick()
    assert http.sent == []                                       # trop tôt
    clock.t += 3600
    bot.tick()
    assert http.sent and "1 nouvelle(s)" in http.texts()[-1]


def test_pilot_document_errors(pilot):
    bot, http, _ = make(pilot, files={"bad": b"pas un fit"})
    assert "trop gros" in bot._document(ME, {"file_id": "x", "file_name": "a.fit", "file_size": 30 * 1024 * 1024})
    assert "pas pu lire" in bot._document(ME, {"file_id": "bad", "file_name": "a.fit"})


def test_pilot_plan_errors_and_refusals(pilot, monkeypatch):
    bot, http, _ = make(pilot)
    monkeypatch.setattr(pilot.repo, "propose_taper", lambda: (_ for _ in ()).throw(LookupError("pas d'historique")))
    assert "pas encore construire" in bot._plan(ME)

    class Verdict:
        allowed = False

    monkeypatch.setattr(pilot.repo, "propose_taper", lambda: ([], Verdict(), None))
    monkeypatch.setattr(pilot, "propose_text", lambda plan, v, ti: "R1 violée")
    assert "garde-fous" in bot._plan(ME) and "R1 violée" in bot._plan(ME)


def test_pilot_confirm_plan_unclear_no_and_calendar_failure(pilot, monkeypatch):
    fill_history(pilot.store)
    bot, http, _ = make(pilot)
    bot.handle_update(upd("/plan"))
    assert "(oui / non)" in http.texts()[-1]
    bot.handle_update(upd("bof"))
    assert "Réponds « oui »" in http.texts()[-1]
    bot.handle_update(upd("non"))
    assert "je n'écris rien" in http.texts()[-1] and pilot.store.get("pending_plan") is None
    bot.handle_update(upd("/plan"))
    monkeypatch.setattr(pilot, "write_plan", lambda plan: (_ for _ in ()).throw(OSError("Google en panne")))
    bot.handle_update(upd("oui"))
    assert "Google en panne" in http.texts()[-1]


def test_pilot_dispatch_commands(pilot):
    bot, http, _ = make(pilot)
    bot.handle_update({"update_id": 1})
    bot.handle_update(upd("salut", chat=STRANGER))
    assert "non autorisé" in http.texts()[-1]
    bot.handle_update(upd("/start"))
    assert "Mode pilote" in http.texts()[-1]
    bot.handle_update(upd("/etat"))
    assert http.texts()[-1].startswith("<pre>")
    bot.handle_update(upd("/sync"))
    assert "pas connecté" in http.texts()[-1]
    pilot.strava = FakeStravaMCP([])
    bot.handle_update(upd("/sync"))
    assert "0 nouvelle(s)" in http.texts()[-1]
    bot.handle_update(upd("/debug"))
    bot.handle_update(upd("Je suis en forme aujourd'hui"))
    assert "<i>" in http.texts()[-1]
    bot.handle_update(upd("/agenda"))
    assert "<pre>" in http.texts()[-1] and bot._aid(ME) == "pilote-0"


def test_pilot_run_forever_ticks_retries_and_reports(pilot):
    bot, http, sleeps = make(pilot, updates=[OSError("coupure"), [upd("/etat", uid=7)]])
    real = bot.handle_update
    bot.handle_update = lambda u: (_ for _ in ()).throw(KeyError("x")) if u["update_id"] == 7 else real(u)
    with pytest.raises(Stop):
        bot.run_forever()
    assert sleeps == [3] and "Erreur interne : KeyError" in http.texts()[-1]
