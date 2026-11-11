"""Synchro Strava par l'API officielle (v0.12) : OAuth 2.0 classique, lecture seule.

Mise en place (une fois, par l'exploitant de l'application) : créer une application sur
strava.com/settings/api -> Client ID + Client Secret (COACH_STRAVA_CLIENT_ID / _SECRET).
« Authorization Callback Domain » = le domaine par lequel on joint l'application
(Tailscale : ton-mac.tailnet-xxxx.ts.net) ; localhost et 127.0.0.1 sont toujours acceptés.

L'athlète, lui, clique sur « Connecter Strava » dans l'application : redirection vers Strava,
autorisation (portée `activity:read_all`), retour sur /strava/callback, échange du code.

Conditions Strava (accord API, nov. 2024) : données affichées à l'athlète seul ; aucun
entraînement de modèle d'IA avec ces données. Ici : usage personnel, analyse par le coach
pour cet athlète, rien n'est réutilisé ailleurs.

Frugalité / quotas (100 lectures par 15 min) : la liste des sorties contient déjà la FC
moyenne -> charge calculée sans flux ; flux FC seulement pour les 30 sorties récentes.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .config import PilotSettings
from .ingest import ImportReport
from .store import PilotStore
from .strava_mcp import SyncResult, activity_from_mcp

log = logging.getLogger("coach.strava")
AUTHORIZE = "https://www.strava.com/oauth/authorize"
TOKEN = "https://www.strava.com/oauth/token"
API = "https://www.strava.com/api/v3"
# v0.50 : profile:read_all pour lire les zones FC (GET /athlete/zones) : les comptes connectés avant doivent
# reconnecter Strava (Carrière › Réglages › Mes zones le propose) ; sans lui, tout le reste fonctionne.
SCOPE = "read,activity:read_all,profile:read_all"
ZONES_SCOPE = "profile:read_all"
MAX_STREAMS = 30
PER_PAGE = 200                     # maximum accepté par Strava : 1 lecture pour 200 sorties
HISTORY_YEARS = 5                  # v0.18 : historique long (résumés seulement, FC détaillée pour le récent)

# http(method, url, params, data, headers) -> (status, json)
Http = Callable[[str, str, dict | None, dict | None, dict | None], tuple[int, object]]


class StravaAuthError(RuntimeError):
    pass


class StravaRateLimited(RuntimeError):
    pass


def urllib_http(method: str, url: str, params: dict | None = None, data: dict | None = None,
                headers: dict | None = None) -> tuple[int, object]:
    """Appel HTTP via httpx (certificats `certifi` fournis : le Python de python.org sur macOS n'a pas
    les certificats système, d'où un CERTIFICATE_VERIFY_FAILED avec urllib)."""
    import httpx
    try:
        r = httpx.request(method, url, params=params, data=data, headers=headers, timeout=60)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Strava injoignable ({type(e).__name__} : {str(e)[:150]})") from e
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class TokenFile:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def write(self, d: dict) -> None:
        self.path.write_text(json.dumps(d))
        self.path.chmod(0o600)


class StravaAPI:
    """Client minimal de l'API v3 (jeton rafraîchi automatiquement)."""
    kind = "api"

    def __init__(self, home: Path, client_id: str, client_secret: str, http: Http = urllib_http,
                 clock: Callable[[], float] = time.time):
        self.client_id, self.client_secret, self.http = client_id, client_secret, http
        self.clock = clock                                # horloge de l'expiration du jeton (injectable : tests)
        self.tokens = TokenFile(home / "strava_api.json")

    def connected(self) -> bool:
        return bool(self.tokens.read().get("refresh_token"))

    def _access_token(self) -> str:
        t = self.tokens.read()
        if not t.get("refresh_token"):
            raise StravaAuthError("Strava n'est pas connecté")
        if t.get("expires_at", 0) - 120 < self.clock():
            status, js = self.http("POST", TOKEN, None, {"client_id": self.client_id,
                                                         "client_secret": self.client_secret,
                                                         "grant_type": "refresh_token",
                                                         "refresh_token": t["refresh_token"]}, None)
            if status != 200 or not isinstance(js, dict) or "access_token" not in js:
                raise StravaAuthError("connexion Strava expirée : reconnecte Strava (État → Reconnecter Strava)")
            t.update({k: js[k] for k in ("access_token", "refresh_token", "expires_at") if k in js})
            self.tokens.write(t)
        return t["access_token"]

    def get(self, path: str, params: dict | None = None):
        status, js = self.http("GET", API + path, params, None, {"Authorization": f"Bearer {self._access_token()}"})
        if status == 429:
            raise StravaRateLimited("quota Strava atteint (100 lectures par 15 min) : la synchro reprendra plus tard")
        if status == 401:
            raise StravaAuthError("accès Strava refusé : reconnecte Strava (État → Reconnecter Strava)")
        if status >= 400:
            raise RuntimeError(f"Strava {status} : {str(js)[:200]}")
        return js

    def athlete(self) -> dict:
        return self.get("/athlete")

    def scopes(self) -> set[str]:
        """Autorisations accordées à la connexion (None pour les jetons d'avant la v0.12 : inconnues)."""
        return {x.strip() for x in str(self.tokens.read().get("scope") or "").split(",") if x.strip()}

    def zones(self) -> dict:
        """v0.50 : zones FC de l'athlète sur Strava (autorisation profile:read_all)."""
        return self.get("/athlete/zones")


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def to_item(a: dict) -> dict:
    """Activité de l'API -> forme commune (celle du connecteur MCP) pour `activity_from_mcp`."""
    return {"id": str(a["id"]), "name": a.get("name", ""), "sport_type": a.get("sport_type") or a.get("type"),
            "start_local": a["start_date_local"].replace("Z", "")[:19],
            "start_utc": _ts(a["start_date"]).astimezone(UTC).isoformat(),
            "summary": {"distance": a.get("distance"), "moving_time": a.get("moving_time"),
                        "elapsed_time": a.get("elapsed_time"), "elevation_gain": a.get("total_elevation_gain"),
                        "average_heartrate": a.get("average_heartrate")}}


def to_streams(js) -> dict | None:
    if not isinstance(js, dict):
        return None
    t, hr = (js.get("time") or {}).get("data"), (js.get("heartrate") or {}).get("data")
    if not (t and hr):
        return None
    out = {"time": t, "heart_rate": hr}
    for k in ("distance", "altitude"):                       # v0.20 : durabilité (découplage FC / allure)
        v = (js.get(k) or {}).get("data")
        if v and len(v) == len(t):
            out[k] = v
    return out


STREAM_KEYS = "time,heartrate,distance,altitude"


def record_decoupling(store: PilotStore, a: dict, streams: dict | None) -> None:
    """Sorties de 90 min et plus : découplage FC / allure gardé pour le radar (kv `decoupling`)."""
    from ..radar import decoupling
    if not streams or (a.get("moving_time") or 0) < 90 * 60:
        return
    dec = decoupling(streams)
    if dec is None:
        return
    items = store.get("decoupling") or {}
    items[str(a["id"])] = {"day": a["start_date_local"][:10], "dec": dec, "name": a.get("name", ""),
                           "min": round((a.get("moving_time") or 0) / 60)}
    store.set("decoupling", dict(sorted(items.items(), key=lambda kv: kv[1]["day"])[-40:]))


def durability_backfill(store: PilotStore, api: StravaAPI, today: date | None = None, max_streams: int = 5) -> int:
    """Une fois : découplage des dernières sorties longues déjà importées (avant la v0.20). ≤ 6 lectures."""
    today = today or date.today()
    after = int(datetime.combine(today - timedelta(days=120), datetime.min.time(), UTC).timestamp())
    try:
        acts = api.get("/athlete/activities", {"after": after, "per_page": PER_PAGE, "page": 1}) or []
        longs = [a for a in acts if a.get("has_heartrate") and (a.get("moving_time") or 0) >= 90 * 60
                 and (a.get("sport_type") or a.get("type")) in ("Run", "TrailRun")]
        n = 0
        for a in sorted(longs, key=lambda x: x["start_date"])[-max_streams:]:
            record_decoupling(store, a, to_streams(api.get(f"/activities/{a['id']}/streams",
                                                           {"keys": STREAM_KEYS, "key_by_type": "true"})))
            n += 1
    except (StravaRateLimited, RuntimeError):
        return 0                                              # on réessaiera à la prochaine synchro
    store.set("decoupling_backfilled", True)
    return n


def sync_strava_api(store: PilotStore, st: PilotSettings, api: StravaAPI, days: int = 120,
                    today: date | None = None, max_new: int = 600, stream_days: int | None = 42,
                    progress: Callable[[str], None] | None = None,
                    now: Callable[[], datetime] = datetime.now) -> SyncResult:
    """Sorties depuis la dernière synchro (ou `days` jours au 1er passage), de la plus ancienne à la plus récente.
    `progress` reçoit un message lisible (« Lecture de tes sorties : mars 2026… »)."""
    from ..onboarding import month_label
    say = progress or (lambda _m: None)
    today = today or date.today()
    after = store.get("strava_api_after")
    if after is None:
        after = int(datetime.combine(today - timedelta(days=days), datetime.min.time(), UTC).timestamp())
    items, page, partial = [], 1, None
    try:
        while len(items) < max_new:
            batch = api.get("/athlete/activities", {"after": after, "per_page": PER_PAGE, "page": page})
            if not batch:
                break
            items += batch
            last = max(date.fromisoformat(a["start_date_local"][:10]) for a in batch)
            say(f"Lecture de tes sorties : {month_label(last)}… ({len(items)} sorties)")
            if len(batch) < PER_PAGE:
                break
            page += 1
    except StravaRateLimited as e:
        partial = str(e)
    items = sorted(items, key=lambda a: a["start_date"])[:max_new]
    cut = today - timedelta(days=stream_days) if stream_days is not None else date.min
    recent = [a for a in items if a.get("has_heartrate") and date.fromisoformat(a["start_date_local"][:10]) >= cut]
    want = {a["id"] for a in recent[-MAX_STREAMS:]}
    rep = ImportReport()
    n_streams = 0
    for a in items:
        streams = None
        if a["id"] in want and partial is None:
            n_streams += 1
            say(f"Fréquence cardiaque des sorties récentes : {month_label(date.fromisoformat(a['start_date_local'][:10]))}"
                f" ({n_streams}/{len(want)})")
            try:
                streams = to_streams(api.get(f"/activities/{a['id']}/streams",
                                             {"keys": STREAM_KEYS, "key_by_type": "true"}))
                record_decoupling(store, a, streams)
            except StravaRateLimited as e:
                partial = str(e)                      # on garde la FC moyenne pour la suite
            except RuntimeError as e:                 # flux absent (activité manuelle...) : FC moyenne
                log.info("flux %s : %s", a["id"], e)
        act = activity_from_mcp(to_item(a), streams, st)
        rep.add(store.add_activity(act), act.load_source)
        store.set("strava_api_after", int(_ts(a["start_date"]).timestamp()) + 1)
    if partial is None:
        refresh_recent_names(store, api, today)
    store.set("strava_last_sync", now().isoformat(timespec="minutes"))
    return SyncResult(rep, len(items), partial)


RENAME_DAYS = 10


def refresh_recent_names(store: PilotStore, api: StravaAPI, today: date | None = None) -> int:
    """v0.45 : sur Strava, une sortie s'appelle d'abord « Evening Run » puis on la renomme (« 10×300 m ») ;
    la synchro qui l'a importée garde l'ancien nom. Une lecture de plus par synchro remet à jour les noms
    des 10 derniers jours."""
    today = today or date.today()
    after = int(datetime.combine(today - timedelta(days=RENAME_DAYS), datetime.min.time(), UTC).timestamp())
    try:
        acts = api.get("/athlete/activities", {"after": after, "per_page": PER_PAGE, "page": 1}) or []
    except (StravaRateLimited, RuntimeError) as e:
        log.info("noms des sorties récentes : %s", e)
        return 0
    names = {_ts(a["start_date"]).astimezone(UTC).strftime("%Y%m%dT%H%M"): a.get("name") or ""
             for a in acts if a.get("start_date") and a.get("name")}
    return store.rename_activities(names)


def backfill_strava_api(store: PilotStore, st: PilotSettings, api: StravaAPI, years: int = HISTORY_YEARS,
                        today: date | None = None, progress: Callable[[str], None] | None = None) -> SyncResult:
    """v0.18 : complète l'historique vers le passé (comptes synchronisés avant, sur 9 mois seulement).
    Résumés uniquement (pas de flux FC : la FC moyenne suffit pour la charge des sorties anciennes).
    Marque kv `strava_history_years` une fois fini ; en cas de quota atteint, recommence à la synchro suivante
    (les sorties déjà importées sont ignorées)."""
    from ..onboarding import month_label
    say = progress or (lambda _m: None)
    today = today or date.today()
    acts = store.activities()
    rep = ImportReport()
    if not acts:
        return SyncResult(rep, 0, None)
    before = int(min(a.start_utc for a in acts).timestamp())
    after = int(datetime.combine(today - timedelta(days=365 * years + years // 4), datetime.min.time(),
                                 UTC).timestamp())
    if before <= after:
        store.set("strava_history_years", years)
        return SyncResult(rep, 0, None)
    items, page, partial = [], 1, None
    try:
        while True:
            batch = api.get("/athlete/activities", {"after": after, "before": before, "per_page": PER_PAGE,
                                                    "page": page})
            if not batch:
                break
            items += batch
            oldest = min(date.fromisoformat(a["start_date_local"][:10]) for a in batch)
            say(f"Lecture de ton historique : {month_label(oldest)}… ({len(items)} sorties de plus)")
            if len(batch) < PER_PAGE:
                break
            page += 1
    except StravaRateLimited as e:
        partial = str(e)
    for a in sorted(items, key=lambda x: x["start_date"]):
        act = activity_from_mcp(to_item(a), None, st)
        rep.add(store.add_activity(act), act.load_source)
    if partial is None:
        store.set("strava_history_years", years)
    return SyncResult(rep, len(items), partial)


class StravaAPIConnector:
    """« Connecter Strava » dans l'application : URL d'autorisation, puis échange du code au retour."""

    def __init__(self, home: Path, client_id: str, client_secret: str, http: Http = urllib_http,
                 clock: Callable[[], float] = time.time):
        self.home, self.client_id, self.client_secret, self.http = home, client_id, client_secret, http
        self.clock = clock                                # expiration des autorisations en attente (tests)
        self.tokens = TokenFile(home / "strava_api.json")
        self.status, self.error = "idle", None
        self._pending: dict[str, tuple[str, Callable[[dict], None], float]] = {}
        self._lock = threading.Lock()

    def connected(self) -> bool:
        return bool(self.tokens.read().get("refresh_token"))

    def start(self, redirect_uri: str, on_done: Callable[[dict], None]) -> dict:
        state = secrets.token_urlsafe(24)
        with self._lock:
            now = self.clock()
            self._pending = {k: v for k, v in self._pending.items() if now - v[2] < 900}
            self._pending[state] = (redirect_uri, on_done, now)
        self.status, self.error = "waiting", None
        url = AUTHORIZE + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "approval_prompt": "auto", "scope": SCOPE, "state": state})
        return {"status": "redirect", "url": url}

    def callback(self, code: str | None, state: str | None, error: str | None, scope: str | None = None) -> bool:
        with self._lock:
            pend = self._pending.pop(state or "", None)
        if pend is None:
            return False                                  # state inconnu ou expiré (anti-CSRF)
        _, on_done, _ = pend
        if error or not code:
            self.status, self.error = "error", "Autorisation Strava refusée."
            return False
        if scope is not None and "activity:read" not in scope:
            self.status, self.error = "error", ("Il faut cocher l'accès à tes activités (y compris privées) "
                                                "sur la page Strava. Réessaie.")
            return False
        try:
            status, js = self.http("POST", TOKEN, None, {"client_id": self.client_id,
                                                         "client_secret": self.client_secret,
                                                         "code": code, "grant_type": "authorization_code"}, None)
        except Exception as e:                            # noqa: BLE001 (réseau, certificats...)
            log.exception("échange du code Strava")
            self.status, self.error = "error", f"Échange du code Strava impossible : {str(e)[:200]}"
            return False
        if status != 200 or not isinstance(js, dict) or "refresh_token" not in js:
            msg = (js or {}).get("message") if isinstance(js, dict) else None
            hint = " Vérifie le Client ID et le Client Secret (~/.ai_coach/app.env)." if status in (400, 401) else ""
            self.status, self.error = "error", (f"Échange du code Strava impossible ({status}"
                                                f"{' : ' + msg if msg else ''}).{hint}")
            return False
        self.tokens.write({k: js.get(k) for k in ("access_token", "refresh_token", "expires_at")}
                          | {"athlete_id": (js.get("athlete") or {}).get("id"), "scope": scope})
        self.status = "connected"
        try:
            on_done(js.get("athlete") or {})
        except Exception:                                 # noqa: BLE001
            log.exception("après connexion Strava")
        return True
