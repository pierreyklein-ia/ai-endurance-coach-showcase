"""Connecteur Strava MCP sans réseau : réponses MCP, jetons OAuth, retour OAuth local, session MCP simulée."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.request
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from coach.pilot import strava_mcp as sm
from coach.pilot.store import PilotStore
from tests.test_pilot import ST


def text(t):
    return SimpleNamespace(text=t)


def test_payload_structured_text_empty_and_error():
    assert sm._payload(SimpleNamespace(isError=False, structuredContent={"a": 1}, content=[])) == {"a": 1}
    assert sm._payload(SimpleNamespace(structuredContent=None, content=[SimpleNamespace(), text('{"b": 2}')])) == {"b": 2}
    assert sm._payload(SimpleNamespace(structuredContent=None, content=[])) == {}
    with pytest.raises(RuntimeError, match="quota"):
        sm._payload(SimpleNamespace(isError=True, content=[text("quota"), text("dépassé")]))


def test_file_token_storage_roundtrip(tmp_path):
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
    st = sm.FileTokenStorage(tmp_path / "t.json")

    async def go():
        assert await st.get_tokens() is None and await st.get_client_info() is None
        await st.set_tokens(OAuthToken(access_token="a", token_type="Bearer"))
        await st.set_client_info(OAuthClientInformationFull(client_id="c", redirect_uris=["http://localhost/cb"]))
        return await st.get_tokens(), await st.get_client_info()

    tok, info = asyncio.run(go())
    assert tok.access_token == "a" and info.client_id == "c"
    assert oct((tmp_path / "t.json").stat().st_mode)[-3:] == "600"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hit(port, query):
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{query}", timeout=2).read()
            return
        except OSError:
            threading.Event().wait(0.05)


def test_wait_for_callback_gets_code_on_injected_port():
    port = free_port()
    t = threading.Thread(target=_hit, args=(port, "code=abc&state=xyz"))
    t.start()
    assert sm._wait_for_callback(timeout=10, port=port) == ("abc", "xyz")
    t.join()


def test_wait_for_callback_error_and_timeout():
    port = free_port()
    t = threading.Thread(target=_hit, args=(port, "error=access_denied"))
    t.start()
    with pytest.raises(RuntimeError, match="access_denied"):
        sm._wait_for_callback(timeout=10, port=port)
    t.join()
    ticks = iter([0.0, 0.0, 999.0])                       # horloge injectée : délai dépassé sans attendre
    with pytest.raises(RuntimeError, match="délai"):
        sm._wait_for_callback(timeout=5, port=free_port(), clock=lambda: next(ticks))


def test_new_redirect_uri_resets_registration(tmp_path):
    (tmp_path / "strava_mcp.json").write_text(json.dumps({"client": {"redirect_uris": ["http://old/cb"]},
                                                          "tokens": {"access_token": "x"}}))
    c = sm.StravaMCP(tmp_path, redirect_uri="http://localhost:8765/callback")
    assert not c.connected()
    (tmp_path / "strava_mcp.json").write_text(json.dumps({"client": {"redirect_uris": ["http://app/cb"]},
                                                          "tokens": {"access_token": "x"}}))
    assert sm.StravaMCP(tmp_path, redirect_uri="http://app/cb").connected()


def test_auth_redirect_and_callback_hooks(tmp_path):
    opened, seen = [], []
    c = sm.StravaMCP(tmp_path, open_browser=opened.append)
    prov = c._auth()
    ctx = prov.context
    asyncio.run(ctx.redirect_handler("https://strava/authorize"))
    assert opened == ["https://strava/authorize"]
    assert str(ctx.client_metadata.redirect_uris[0]) == sm.REDIRECT_URI
    c2 = sm.StravaMCP(tmp_path, redirect_uri="https://app/cb", on_redirect=seen.append, on_callback=lambda: ("k", "s"))
    ctx2 = c2._auth().context
    asyncio.run(ctx2.redirect_handler("u"))
    assert seen == ["u"] and asyncio.run(ctx2.callback_handler()) == ("k", "s")
    headless = sm.StravaMCP(tmp_path, interactive=False)
    with pytest.raises(RuntimeError, match="expirée"):
        asyncio.run(headless._auth().context.redirect_handler("u"))


def test_default_callback_uses_local_server(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_wait_for_callback", lambda: ("code", None))
    assert asyncio.run(sm.StravaMCP(tmp_path)._auth().context.callback_handler()) == ("code", None)


class FakeSession:
    """Remplace mcp.ClientSession : outils préfixés, une erreur au 3e appel."""
    calls: list = []

    def __init__(self, r, w):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="strava__list_activities"),
                                      SimpleNamespace(name="get_activity_streams")])

    async def call_tool(self, name, args):
        FakeSession.calls.append(name)
        if args.get("boom"):
            raise RuntimeError("quota")
        return SimpleNamespace(isError=False, structuredContent={"tool": name, **args}, content=[])


@asynccontextmanager
async def fake_transport(url, http_client=None):
    yield None, None, None


def test_run_batch_and_call_with_fake_mcp_session(tmp_path, monkeypatch):
    import mcp
    import mcp.client.streamable_http as sh
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)
    monkeypatch.setattr(sh, "streamable_http_client", fake_transport)
    FakeSession.calls = []
    c = sm.StravaMCP(tmp_path)
    out = c.batch([("list_activities", {"a": 1}), ("get_activity_streams", {"b": 2}), ("x", {"boom": True}),
                   ("jamais", {})], pause_s=0)
    assert out[0] == {"tool": "strava__list_activities", "a": 1} and out[1]["tool"] == "get_activity_streams"
    assert isinstance(out[2], RuntimeError) and len(out) == 3            # on garde le partiel
    assert c.call("list_activities", {"z": 3})["z"] == 3
    with pytest.raises(RuntimeError):
        c.call("x", {"boom": True})


# --------------------------------------------------------------------------- synchro incrémentale
class FakeClient:
    def __init__(self, pages, streams=None):
        self.pages, self.streams, self.args = list(pages), streams, []

    def call(self, name, args):
        self.args.append(args)
        return self.pages.pop(0)

    def batch(self, calls):
        self.batched = calls
        return self.streams if self.streams is not None else [{} for _ in calls]


def it(i, day):
    return {"id": i, "start_local": f"{day.isoformat()}T07:00:00", "name": f"S{i}", "sport_type": "TrailRun",
            "summary": {"elapsed_time": 3000, "moving_time": 3000, "distance": 9000, "average_heartrate": 140}}


def test_sync_paginates_limits_streams_to_recent_and_stamps_with_injected_now(tmp_path):
    today = date(2026, 9, 30)
    store = PilotStore(tmp_path / "p.db")
    old, new = it(1, today - timedelta(days=60)), it(2, today - timedelta(days=2))
    client = FakeClient([{"activities": [old], "has_next_page": True, "end_cursor": "c1"},
                         {"activities": [new], "has_next_page": False}])
    res = sm.sync_strava(store, ST, client, today=today, stream_days=14, now=lambda: datetime(2026, 9, 30, 8, 5))
    assert res.listed == 2 and res.report.new == 2 and res.partial is None
    assert client.args[1]["after"] == "c1" and len(client.batched) == 1          # FC seulement pour la récente
    assert store.get("strava_last_sync") == "2026-09-30T08:05"
    assert store.get("strava_last_start_local") == new["start_local"]
    client2 = FakeClient([{"activities": [], "has_next_page": False}])
    sm.sync_strava(store, ST, client2, today=today)
    assert client2.args[0]["range_start"] == "2026-09-28T07:00:01"               # reprise après la dernière


def test_sync_stops_when_streams_are_missing(tmp_path):
    today = date(2026, 9, 30)
    store = PilotStore(tmp_path / "p.db")
    client = FakeClient([{"activities": [it(1, today), it(2, today)], "has_next_page": False}], streams=[{}])
    res = sm.sync_strava(store, ST, client, today=today)
    assert res.partial == "interrompu" and res.report.new == 1


def test_activity_from_mcp_prorates_load_when_watch_left_running():
    n = 200
    streams = {"time": list(range(0, n * 30, 30)), "heart_rate": [150] * n}
    item = {"start_local": "2026-09-29T07:00:00", "start_utc": "2026-09-29T05:00:00+00:00", "sport_type": "Run",
            "name": "Montre oubliée", "summary": {"elapsed_time": 24 * 3600, "moving_time": 3000, "distance": 9000}}
    a = sm.activity_from_mcp(item, streams, ST)
    assert a.duration_s == 3000 and a.load and a.load_source
    full = sm.activity_from_mcp({**item, "summary": {"elapsed_time": n * 30, "moving_time": n * 30}}, streams, ST)
    assert a.load < full.load
