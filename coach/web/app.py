"""Application web installable (PWA) : API FastAPI + interface statique.

    python -m coach.web serve --claude

Toutes les routes /api/* (sauf /api/login) exigent le cookie de session.
L'interface (HTML/JS/CSS, service worker, manifeste) est servie sans session :
elle affiche l'écran de connexion quand l'API répond 401.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..persona import CoachPersona
from ..stt import MAX_BYTES as stt_max
from ..stt import Transcriber, suffix_for
from . import auth
from .service import CoachService, MoveRefused

log = logging.getLogger("coach.web")
STATIC = Path(__file__).parent / "static"


class LoginIn(BaseModel):
    passcode: str = Field(max_length=200)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class FeedbackIn(BaseModel):
    status: str
    rpe: int | None = Field(default=None, ge=1, le=10)
    note: str = Field(default="", max_length=2000)
    notify_coach: bool = False


class FeelingIn(BaseModel):
    week_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    fatigue: int = Field(ge=1, le=5)
    difficulty: int = Field(ge=1, le=5)
    fun: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=500)


class LinkIn(BaseModel):
    activity_start: str = Field(min_length=10, max_length=40)


class MoveIn(BaseModel):
    """v0.37 : nouveau jour d'une séance (glisser-déposer dans Semaine)."""
    day: date


class NutritionItemIn(BaseModel):
    id: str = Field(max_length=40)
    qty: float = Field(ge=0, le=60)
    carbs_g: float | None = Field(default=None, ge=0, le=200)


class NutritionIn(BaseModel):
    """v0.38 : bilan nutrition d'une sortie ≥ 1 h 30."""
    session_id: str | None = Field(default=None, max_length=120)
    day: date | None = None
    duration_min: float | None = Field(default=None, gt=0, le=48 * 60)
    items: list[NutritionItemIn] = Field(default_factory=list, max_length=60)
    water_ml: float = Field(default=0, ge=0, le=20000)
    digestion: int = Field(ge=1, le=5)
    symptoms: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=1000)
    rpe: int | None = Field(default=None, ge=1, le=10)
    note: str = Field(default="", max_length=2000)


class RacePointIn(BaseModel):
    name: str = Field(default="", max_length=60)
    km: float = Field(gt=0, le=1000)


class RacePointsIn(BaseModel):
    """v0.38 : ravitos d'une course sans fichier de course (simulateur)."""
    points: list[RacePointIn] = Field(default_factory=list, max_length=30)


class BirthdayIn(BaseModel):
    day: int | None = Field(default=None, ge=1, le=31)
    month: int | None = Field(default=None, ge=1, le=12)


class WeekMaxIn(BaseModel):
    """v0.49 : volume maximum par semaine (vide = pas de plafond sur cet axe)."""
    hours: float | None = Field(default=None, ge=0, le=40)
    km: float | None = Field(default=None, ge=0, le=400)
    dplus: int | None = Field(default=None, ge=0, le=20000)


class ZonesIn(BaseModel):
    """v0.50 : « Mes zones » : valeurs saisies (null = revenir à l'automatique ; champ absent = inchangé)."""
    hr_max: float | None = Field(default=None, ge=0, le=240)
    hr_rest: float | None = Field(default=None, ge=0, le=140)
    lthr: float | None = Field(default=None, ge=0, le=240)


class WeekStartIn(BaseModel):
    day: int = Field(ge=0, le=6)


class StrengthIn(BaseModel):
    exercise: str = Field(max_length=20)
    kg: float
    reps: int = 1
    bodyweight: float
    day: str | None = Field(default=None, max_length=10)


class CoachIn(CoachPersona):
    """v0.36 : choix du coach (genre, personnalité, prénom, tu / vous)."""
    model_config = {"extra": "forbid"}


class PushSub(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: dict


def create_app(service: CoachService, *, passcode: str, secret: bytes, public_key: str | None = None,
               background: bool = True, tick_every_s: int = 60, stt: Transcriber | None = None,
               static_dir: Path = STATIC) -> FastAPI:
    throttle = auth.Throttle()
    stt = stt or Transcriber()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if background:
            stt.warm_up()                                     # v0.21 : modèle de dictée chargé d'avance
            async def loop():
                while True:
                    await asyncio.sleep(tick_every_s)
                    try:
                        await asyncio.to_thread(service.tick)
                    except Exception:
                        log.exception("tâche de fond")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="AI Endurance Coach", lifespan=lifespan, docs_url=None, redoc_url=None)

    def session(request: Request) -> str:
        sub = auth.verify(secret, request.cookies.get(auth.COOKIE))
        if not sub:
            raise HTTPException(401, "connexion requise")
        return sub

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; manifest-src 'self'; worker-src 'self'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ session
    @app.post("/api/login")
    def login(body: LoginIn, request: Request, response: Response):
        if not throttle.allowed():
            raise HTTPException(429, "trop d'essais : réessaie dans une minute")
        ok = auth.check_passcode(body.passcode, passcode)
        throttle.record(ok)
        if not ok:
            raise HTTPException(401, "code incorrect")
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(auth.COOKIE, auth.sign(secret), max_age=auth.SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", secure=https, path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    # ------------------------------------------------------------ vues
    @app.get("/api/me")
    def me(_: str = Depends(session)):
        return {**service.me(), "push_public_key": public_key}

    @app.get("/api/coach")
    def coach_get(_: str = Depends(session)):
        return service.coach_persona()

    @app.post("/api/coach")
    def coach_set(body: CoachIn, _: str = Depends(session)):
        return service.set_coach_persona(CoachPersona.model_validate(body.model_dump()))

    @app.get("/api/about")
    def about(_: str = Depends(session)):
        return service.about(stt.status())

    @app.post("/api/about/claude-check")
    def claude_check(_: str = Depends(session)):
        return service.check_claude()

    @app.post("/api/about/image-check")
    def image_check(_: str = Depends(session)):
        return service.check_image_key()

    @app.get("/api/today")
    def today(_: str = Depends(session)):
        started = service.sync_if_stale()                   # v0.52.3 : synchro à l'ouverture de l'appli
        return {**service.today_view(), "syncing": started or service.syncing()}

    @app.get("/api/week")
    def week(start: date | None = None, _: str = Depends(session)):
        return service.week_view(start)

    @app.get("/api/state")
    def state(_: str = Depends(session)):
        return service.state_view()

    @app.get("/api/progress")
    def progress(days: int = 180, _: str = Depends(session)):
        return service.progress_view(days)

    @app.get("/api/activities")
    def activities(before: str | None = None, limit: int = 40, sport: str | None = None, q: str | None = None,
                   offset: int = 0, _: str = Depends(session)):
        return service.activities_page(before, limit, sport, (q or "")[:100], offset)

    # ------------------------------------------------------------ actions
    @app.post("/api/sessions/{session_id}/feedback")
    def feedback(session_id: str, body: FeedbackIn, _: str = Depends(session)):
        try:
            return service.feedback(session_id, body.status, body.rpe, body.note, body.notify_coach)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/sessions/{session_id}/move")
    def move(session_id: str, body: MoveIn, _: str = Depends(session)):
        try:
            return service.move_session(session_id, body.day)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e
        except MoveRefused as e:
            return JSONResponse({"error": e.message, "rules": e.rules}, status_code=409)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e

    # ------------------------------------------------------------ nutrition (v0.38)
    @app.get("/api/nutrition/foods")
    def nutrition_foods(_: str = Depends(session)):
        return service.nutrition_foods()

    @app.get("/api/nutrition")
    def nutrition(_: str = Depends(session)):
        return service.nutrition_view()

    @app.post("/api/nutrition")
    def nutrition_save(body: NutritionIn, _: str = Depends(session)):
        try:
            return service.save_nutrition(session_id=body.session_id, items=[i.model_dump() for i in body.items],
                                          water_ml=body.water_ml, digestion=body.digestion, symptoms=body.symptoms,
                                          comment=body.comment, duration_min=body.duration_min, day=body.day,
                                          rpe=body.rpe, note=body.note)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    # ------------------------------------------------------------ simulateur de course (v0.38)
    @app.get("/api/race/sim")
    def race_sim(heat: str = "normale", form: float = 0.0, target: str | None = None, _: str = Depends(session)):
        try:
            return service.race_sim(heat, max(-5.0, min(5.0, form)) / 100, target)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/race/gpx")
    async def race_gpx(request: Request, name: str = "trace.gpx", _: str = Depends(session)):
        from .race_flow import GPX_MAX
        if int(request.headers.get("content-length") or 0) > GPX_MAX:
            raise HTTPException(413, "GPX trop gros (15 Mo au plus)")
        data = await request.body()
        try:
            return await asyncio.to_thread(service.upload_race_gpx, data, name)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.delete("/api/race/gpx")
    def race_gpx_delete(_: str = Depends(session)):
        return service.delete_race_gpx()

    @app.post("/api/race/points")
    def race_points(body: RacePointsIn, _: str = Depends(session)):
        try:
            return service.set_race_points([p.model_dump() for p in body.points])
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    # ------------------------------------------------------------ Œil du coach (v0.40)
    @app.get("/api/eye")
    def eye_status(_: str = Depends(session)):
        return service.eye_status()

    @app.post("/api/eye")
    def eye_start(_: str = Depends(session)):
        from .eye_flow import EyeRefused
        try:
            return service.eye_start()
        except EyeRefused as e:
            st = {k: v for k, v in e.status.items() if k not in ("last", "history")}
            return JSONResponse({"error": e.message, **st}, status_code=409)

    # v0.44 : lier / délier une séance et la sortie réellement faite
    @app.get("/api/sessions/{session_id}/link")
    def link_candidates(session_id: str, _: str = Depends(session)):
        try:
            return service.link_candidates(session_id)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e

    @app.post("/api/sessions/{session_id}/link")
    def link_session(session_id: str, body: LinkIn, _: str = Depends(session)):
        try:
            return service.link_session(session_id, body.activity_start)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e

    @app.delete("/api/sessions/{session_id}/link")
    def unlink_session(session_id: str, _: str = Depends(session)):
        try:
            return service.unlink_session(session_id)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e

    @app.get("/api/goals")
    def goals(_: str = Depends(session)):
        return {"goals": service.goals()}

    @app.get("/api/chat")
    def history(before: int | None = None, _: str = Depends(session)):
        # v0.38 : `typing` = le coach écrit une réaction en tâche de fond (l'interface relit toutes les 2 s)
        return service.history(before=before)

    @app.post("/api/chat")
    def chat(body: ChatIn, _: str = Depends(session)):
        return {"quick": [], **service.chat(body.text)}

    @app.post("/api/upload")
    async def upload(request: Request, name: str, _: str = Depends(session)):
        data = await request.body()
        try:
            return await asyncio.to_thread(service.upload, name, data)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            raise HTTPException(422, f"fichier illisible : {str(e)[:200]}") from e

    # ------------------------------------------------------------ carrière (v0.24)
    @app.get("/api/career")
    def career(_: str = Depends(session)):
        c = service.career_view(); c.pop("_done", None)
        return c

    @app.post("/api/career/face")
    async def career_face(request: Request, _: str = Depends(session)):
        from ..avatar_image import FACE_MAX
        if int(request.headers.get("content-length") or 0) > FACE_MAX:
            raise HTTPException(413, "photo trop lourde (5 Mo au plus)")
        data = await request.body()
        try:
            c = service.set_face(data)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        c.pop("_done", None)
        return c

    @app.delete("/api/career/face")
    def career_face_delete(_: str = Depends(session)):
        c = service.delete_face(); c.pop("_done", None)
        return c

    @app.get("/api/career/avatar.png")
    def career_avatar_png(_: str = Depends(session)):
        f = service.avatar_png()
        if f is None:
            raise HTTPException(404, "pas d'image")
        return FileResponse(f, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/career/avatar/regenerate")
    def career_avatar_regen(_: str = Depends(session)):
        return service.regenerate_avatar()

    @app.post("/api/career/seen")
    def career_seen(_: str = Depends(session)):
        return service.career_seen()

    @app.post("/api/career/avatar")
    def career_avatar(body: dict, _: str = Depends(session)):
        return service.set_avatar(body)

    @app.post("/api/career/birthday")
    def career_birthday(body: BirthdayIn, _: str = Depends(session)):
        try:
            return service.set_birthday(body.day, body.month)
        except ValueError as e:
            raise HTTPException(422, "date invalide") from e

    @app.get("/api/week-max")
    def week_max_get(_: str = Depends(session)):
        return service.week_max_view()

    @app.post("/api/week-max")
    def week_max_set(body: WeekMaxIn, _: str = Depends(session)):
        return service.set_week_max(round(body.hours * 60) if body.hours else None, body.km or None,
                                    body.dplus or None)

    # ------------------------------------------------------------ v0.50 : zones FC, semaine, récap
    @app.get("/api/zones")
    def zones_get(_: str = Depends(session)):
        return service.zones_view()

    @app.post("/api/zones")
    def zones_set(body: ZonesIn, _: str = Depends(session)):
        try:
            return service.set_zones({k: getattr(body, k) for k in body.model_fields_set})
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/week-start")
    def week_start_get(_: str = Depends(session)):
        return service.week_start_view()

    @app.post("/api/week-start")
    def week_start_set(body: WeekStartIn, _: str = Depends(session)):
        return service.set_week_start(body.day)

    @app.get("/api/recap")
    def recap_get(_: str = Depends(session)):
        return service.recap_view()

    @app.post("/api/recap/feeling")
    def recap_feeling(body: FeelingIn, _: str = Depends(session)):
        try:
            return service.set_week_feeling(body.week_start, body.fatigue, body.difficulty, body.fun, body.note)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/career/strength")
    def career_strength(body: StrengthIn, _: str = Depends(session)):
        try:
            c = service.add_strength(body.exercise, body.kg, body.reps, body.bodyweight, body.day)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        c.pop("_done", None)
        return c

    # ------------------------------------------------------------ dictée (v0.21)
    @app.get("/api/dictation")
    def dictation_status(_: str = Depends(session)):
        return stt.status()

    @app.post("/api/dictation")
    async def dictation(request: Request, _: str = Depends(session)):
        if int(request.headers.get("content-length") or 0) > stt_max:
            raise HTTPException(413, "enregistrement trop long (3 min au plus)")
        data = await request.body()
        try:
            text = await asyncio.to_thread(stt.transcribe, data, suffix_for(request.headers.get("content-type")))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:
            log.exception("dictée")
            raise HTTPException(422, "audio illisible") from e
        return {"text": text}

    # ------------------------------------------------------------ nouvel athlète (v0.10)
    @app.get("/api/onboarding")
    def onboarding(_: str = Depends(session)):
        return service.onboarding_view()

    @app.post("/api/strava/connect")
    async def strava_connect(request: Request, _: str = Depends(session)):
        # Adresse de retour = celle par laquelle le navigateur nous joint (Tailscale : https://…ts.net).
        # L'origine envoyée par la page n'est retenue que si elle désigne bien ce serveur (en-tête Host).
        from urllib.parse import urlparse
        try:
            body = await request.json()
        except Exception:
            body = {}
        origin, force = str(body.get("origin") or ""), bool(body.get("force"))
        u = urlparse(origin)
        if not (u.scheme in ("http", "https") and u.netloc and u.netloc == request.headers.get("host")):
            origin = str(request.base_url)
        try:
            return await asyncio.to_thread(service.connect_strava, origin, force)
        except Exception as e:                       # noqa: BLE001
            log.exception("connexion Strava")
            return {"status": "error", "message": f"Connexion Strava impossible : {type(e).__name__} : {str(e)[:200]}"}

    @app.get("/strava/callback", include_in_schema=False)
    def strava_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                        scope: str | None = None):
        # pas de session exigée : la page peut s'ouvrir dans un autre navigateur (iPhone) ;
        # le flux OAuth en attente vérifie `state` avant d'échanger le code.
        try:
            ok = service.connector is not None and service.connector.callback(code, state, error, scope)
        except Exception as e:                       # noqa: BLE001 : toujours une page lisible
            log.exception("retour Strava")
            ok = False
            if service.connector is not None:
                service.connector.error = f"Erreur interne : {type(e).__name__} : {str(e)[:200]}"
        title = ("Strava connecté ✅" if ok and not error else "Connexion Strava interrompue")
        why = getattr(service.connector, "error", None) if service.connector else None
        body = ("Le coach lit ton historique. Reviens dans l'application : ton bilan arrive dans une minute."
                if ok and not error else (why + " " if why else "")
                + "Retourne dans l'application et appuie à nouveau sur « Connecter Strava ».")
        body = body.replace("<", "&lt;")
        html = (f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' "
                f"content='width=device-width, initial-scale=1'><title>{title}</title>"
                f"<link rel='stylesheet' href='/static/styles.css'></head><body><section class='login'>"
                f"<img src='/static/icon.svg' alt='' class='login-logo'><h1>{title}</h1><p class='muted'>{body}</p>"
                f"<a class='btn primary' href='/#coach'>Revenir à l'application</a></section></body></html>")
        return Response(html, media_type="text/html")

    @app.post("/api/onboarding/start")
    def onboarding_start(_: str = Depends(session)):
        try:
            return service.start_conversation()
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/reset")
    def reset(body: dict, _: str = Depends(session)):
        if body.get("confirm") != "recommencer":
            raise HTTPException(422, "confirmation manquante")
        service.reset()
        return {"ok": True}

    @app.post("/api/sync")
    def sync(_: str = Depends(session)):
        return service.sync()

    # ------------------------------------------------------------ notifications
    @app.post("/api/push/subscribe")
    def subscribe(sub: PushSub, _: str = Depends(session)):
        if not public_key:
            raise HTTPException(503, "notifications non configurées sur le serveur")
        service.store.add_push_sub(sub.model_dump())
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def unsubscribe(sub: PushSub, _: str = Depends(session)):
        service.store.remove_push_sub(sub.endpoint)
        return {"ok": True}

    @app.post("/api/push/test")
    def push_test(_: str = Depends(session)):
        n = service.notifier.send("AI Endurance Coach", "Les notifications fonctionnent ✅", "/") \
            if service.notifier else 0
        return {"sent": n}

    # ------------------------------------------------------------ interface
    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(static_dir / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(static_dir / "manifest.webmanifest", media_type="application/manifest+json")

    # interface React (frontend/, construite dans static/app/) ; l'ancienne reste sur /legacy le temps d'une version
    @app.get("/", include_in_schema=False)
    def index():
        built = static_dir / "app" / "index.html"
        page = built if built.is_file() else static_dir / "index.html"
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/legacy", include_in_schema=False)
    def legacy():
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
