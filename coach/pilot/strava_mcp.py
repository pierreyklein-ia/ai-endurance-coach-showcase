"""Synchro Strava via le connecteur MCP OFFICIEL de Strava — usage personnel seulement.

Base légale (Strava API Policy, juin 2026) :
- §3.5 : un abonné peut accéder au Strava MCP pour l'usage personnel de ses
  propres données et « apporter sa propre application d'IA » ;
- §5.3 : l'interdiction des usages IA « ne s'étend pas » au Strava MCP ;
- interdit : tout accès commercial ou pour des tiers. => PILOTE UNIQUEMENT.
  Le produit multi-athlètes passera par l'API Suunto.

Frugalité : le LLM ne voit jamais les flux. On lit la liste des sorties et la
FC (1 appel MCP par nouvelle sortie), la charge est calculée localement.

Réserve : connecteur lancé avec Claude ; l'acceptation d'autres clients OAuth
(enregistrement dynamique) est à confirmer à la première connexion.
"""
from __future__ import annotations

import asyncio
import json
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import numpy as np

from ..load_engine import session_load
from .config import PilotSettings
from .ingest import ImportReport, load_from_avg_hr, sport_label
from .store import Activity, PilotStore, effective_duration

MCP_URL = "https://mcp.strava.com/mcp"
CALLBACK_PORT = 8765
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


# --------------------------------------------------------------------------- #
# Transformation des réponses MCP -> Activity (testable sans réseau)
# --------------------------------------------------------------------------- #
def activity_from_mcp(item: dict, streams: dict | None, st: PilotSettings) -> Activity:
    s = item.get("summary", {})
    tz = ZoneInfo(st.tz)
    local = datetime.fromisoformat(item["start_local"])
    start_utc = (datetime.fromisoformat(item["start_utc"]) if item.get("start_utc") else
                 (local.replace(tzinfo=tz) if local.tzinfo is None else local).astimezone(UTC))
    sport = sport_label({"TrailRun": "trail run"}.get(item.get("sport_type"), item.get("sport_type")))
    # v0.48 : montre laissée en marche (24 h pour 6 km) : le temps en mouvement fait foi (store.effective_duration)
    raw_elapsed = float(s.get("elapsed_time") or s.get("moving_time") or 0)
    elapsed = effective_duration(raw_elapsed, float(s["moving_time"]) if s.get("moving_time") else None)
    avg_hr, load, src = s.get("average_heartrate"), None, None      # FC moyenne : API Strava (v0.12)
    streams = streams or {}
    hr, t = streams.get("heart_rate"), streams.get("time")
    if hr and t and len(hr) == len(t) and len(hr) > 10:
        t_arr = np.array(t, float)
        hr_arr = np.array([np.nan if x is None else x for x in hr], float)
        ok = np.isfinite(hr_arr)
        if ok.sum() > 10:
            t1 = np.arange(0, t_arr[-1], 5.0)
            hr1 = np.interp(t1, t_arr[ok], hr_arr[ok])
            avg_hr = float(np.mean(hr1))
            val, src = session_load(hr1, hr_rest=st.hr_rest, hr_max=st.hr_max, lthr=st.threshold_hr,
                                    duration_min=elapsed / 60, rpe=None, srpe_to_tss=1.0,
                                    sex=st.sex, dt_s=5.0)
            load = round(val, 1) if np.isfinite(val) else None
            if load is not None and elapsed < raw_elapsed - 1:       # v0.48 : flux sur 24 h, charge au prorata
                load = round(load * elapsed / raw_elapsed, 1)
    if load is None:
        load, src = load_from_avg_hr(avg_hr, float(s.get("moving_time") or elapsed), sport, st)
    act = Activity(start_utc, local.date(), sport, elapsed, float(s.get("distance") or 0),
                   float(s.get("elevation_gain") or 0), avg_hr, load, src, "Strava MCP",
                   item.get("name", ""), float(s["moving_time"]) if s.get("moving_time") else None)
    if t and len(t) > 30:                                           # v0.43 : type de séance couru (zones, blocs)
        from ..session_type import profile_from_streams
        act.profile = profile_from_streams(t, hr, streams.get("distance"), streams.get("altitude"),
                                           lthr=st.threshold_hr)
    return act


def _payload(result) -> dict:
    """CallToolResult -> dict (structuredContent ou texte JSON)."""
    if getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"Strava MCP : {text[:300]}")
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    for c in result.content:
        if getattr(c, "text", None):
            return json.loads(c.text)
    return {}


# --------------------------------------------------------------------------- #
# OAuth (enregistrement dynamique + PKCE, géré par le SDK MCP)
# --------------------------------------------------------------------------- #
class FileTokenStorage:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, d: dict) -> None:
        self.path.write_text(json.dumps(d))
        self.path.chmod(0o600)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        d = self._read().get("tokens")
        return OAuthToken.model_validate(d) if d else None

    async def set_tokens(self, tokens) -> None:
        d = self._read()
        d["tokens"] = tokens.model_dump(mode="json")
        self._write(d)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        d = self._read().get("client")
        return OAuthClientInformationFull.model_validate(d) if d else None

    async def set_client_info(self, info) -> None:
        d = self._read()
        d["client"] = info.model_dump(mode="json")
        self._write(d)


def _wait_for_callback(timeout: float = 300, port: int = CALLBACK_PORT,
                       clock: Callable[[], float] = time.time) -> tuple[str, str | None]:
    """Petit serveur local qui attend le retour d'OAuth (`port` et `clock` injectables : tests)."""
    got: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            got["code"], got["state"] = q.get("code", [None])[0], q.get("state", [None])[0]
            got["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Strava connecté ✅</h2>Tu peux fermer cet onglet.".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 1
    deadline = clock() + timeout
    while "code" not in got and "error" not in got and clock() < deadline:
        srv.handle_request()
    srv.server_close()
    if got.get("error") or not got.get("code"):
        raise RuntimeError(f"autorisation Strava refusée ou expirée ({got.get('error') or 'délai dépassé'})")
    return got["code"], got.get("state")


class StravaMCP:
    """Client MCP synchrone minimal (une session par lot d'appels).

    `redirect_uri`/`on_redirect`/`on_callback` : connexion lancée depuis l'application web (v0.11) —
    Strava renvoie le navigateur vers l'application, qui transmet le code au flux OAuth en attente."""

    def __init__(self, home: Path, url: str = MCP_URL, interactive: bool = True, redirect_uri: str | None = None,
                 on_redirect=None, on_callback=None, open_browser: Callable[[str], object] = webbrowser.open):
        self.url, self.interactive, self.open_browser = url, interactive, open_browser
        self.storage = FileTokenStorage(home / "strava_mcp.json")
        self.redirect_uri, self.on_redirect, self.on_callback = redirect_uri, on_redirect, on_callback
        if redirect_uri:                              # nouvelle adresse de retour : nouvel enregistrement
            d = self.storage._read()
            if d.get("client") and redirect_uri not in (d["client"].get("redirect_uris") or []):
                d.pop("client", None)
                d.pop("tokens", None)
                self.storage._write(d)

    def connected(self) -> bool:
        return bool(self.storage._read().get("tokens"))

    def _auth(self):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        async def redirect(url: str) -> None:
            if self.on_redirect:
                return self.on_redirect(url)
            if not self.interactive:
                raise RuntimeError("connexion Strava expirée : reconnecte Strava depuis l'application")
            print(f"\nOuvre cette adresse pour autoriser le coach à lire TES données Strava :\n{url}\n")
            self.open_browser(url)

        async def callback() -> tuple[str, str | None]:
            if self.on_callback:
                return await asyncio.to_thread(self.on_callback)
            return await asyncio.to_thread(_wait_for_callback)

        stored = (self.storage._read().get("client") or {}).get("redirect_uris") or [REDIRECT_URI]
        uri = self.redirect_uri or stored[0]
        meta = OAuthClientMetadata(client_name="AI Endurance Coach (usage personnel)",
                                   redirect_uris=[uri], grant_types=["authorization_code", "refresh_token"],
                                   response_types=["code"], token_endpoint_auth_method="none")
        return OAuthClientProvider(self.url, meta, self.storage, redirect, callback)

    async def _run(self, calls: list[tuple[str, dict]], pause_s: float) -> list:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        out = []
        async with httpx.AsyncClient(auth=self._auth(), timeout=httpx.Timeout(60, read=300)) as http:
            async with streamable_http_client(self.url, http_client=http) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = {t.name for t in (await session.list_tools()).tools}
                    for name, args in calls:
                        real = name if name in tools else next((t for t in tools if t.endswith(name)), name)
                        try:
                            out.append(_payload(await session.call_tool(real, args)))
                        except Exception as e:           # quota atteint... : on garde le partiel
                            out.append(e)
                            break
                        await asyncio.sleep(pause_s)
        return out

    def batch(self, calls: list[tuple[str, dict]], pause_s: float = 0.3) -> list:
        return asyncio.run(self._run(calls, pause_s))

    def call(self, name: str, args: dict) -> dict:
        res = self.batch([(name, args)], pause_s=0)[0]
        if isinstance(res, Exception):
            raise res
        return res


# --------------------------------------------------------------------------- #
# Synchronisation incrémentale
# --------------------------------------------------------------------------- #
@dataclass
class SyncResult:
    report: ImportReport
    listed: int
    partial: str | None = None


def sync_strava(store: PilotStore, st: PilotSettings, client, days: int = 120,
                today: date | None = None, max_new: int = 200, stream_days: int | None = None,
                now: Callable[[], datetime] = datetime.now) -> SyncResult:
    """Récupère les sorties depuis la dernière synchro (ou `days` jours au 1er passage)."""
    today = today or date.today()
    last = store.get("strava_last_start_local")
    start = (datetime.fromisoformat(last) + timedelta(seconds=1)) if last else \
        datetime.combine(today - timedelta(days=days), datetime.min.time())
    items, cursor = [], None
    while True:
        args = {"range_start": start.strftime("%Y-%m-%dT%H:%M:%S"), "ordering": "StartDateLocalAsc",
                "first": 100}
        if cursor:
            args["after"] = cursor
        page = client.call("list_activities", args)
        items += page.get("activities", [])
        cursor = page.get("end_cursor")
        if not page.get("has_next_page") or not cursor or len(items) >= max_new:
            break
    items = items[:max_new]
    # Premier import d'un nouvel athlète : FC seulement pour les sorties récentes (charge
    # fiable pour l'ACWR), résumés seuls pour l'historique (bilan) -> quelques appels au lieu de centaines.
    cut = (today - timedelta(days=stream_days)).isoformat() if stream_days is not None else ""
    recent = [it for it in items if it["start_local"][:10] >= cut]
    old_items = [it for it in items if it["start_local"][:10] < cut]
    calls = [("get_activity_streams", {"activity_id": str(it["id"]), "streams": ["time", "heart_rate"],
                                       "resolution": int(min(3000, max(50, (it.get("summary", {})
                                                                  .get("elapsed_time") or 600) / 5)))})
             for it in recent]
    streams = [None] * len(old_items) + (client.batch(calls) if calls else [])
    items = old_items + recent
    rep, partial = ImportReport(), None
    for i, it in enumerate(items):
        if i >= len(streams) or isinstance(streams[i], Exception):
            partial = str(streams[i]) if i < len(streams) else "interrompu"
            break
        a = activity_from_mcp(it, streams[i], st)
        rep.add(store.add_activity(a), a.load_source)
        store.set("strava_last_start_local", it["start_local"])
    store.set("strava_last_sync", now().isoformat(timespec="minutes"))
    return SyncResult(rep, len(items), partial)
