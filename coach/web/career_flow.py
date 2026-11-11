"""Carrière, avatar et onglet Détails (extrait de service.py en v0.31)."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("coach.web")


class CareerFlow:
    """XP, trophées, avatar et son image, onglet Détails (mixin de CoachService)."""

    def about(self, stt: dict | None = None) -> dict:
        """Onglet Détails (v0.28) : de quoi vérifier que la bonne version tourne, et l'état des options."""
        from .. import avatar_image as ai
        from ..knowledge import KNOWLEDGE
        from ..rules_registry import REGISTRY_VERSION, registry_hash
        from ..version import app_version
        st = self.store.get("avatar_image") or {}
        today = self.today().isoformat()
        return {"version": app_version(), "registry": REGISTRY_VERSION, "registry_hash": registry_hash(),
                "cards": len(KNOWLEDGE), "today": today,
                "dictation": stt or {}, "llm": "Claude" if self.llm_name() == "claude" else "mode simplifié",
                "image": {"enabled": ai.enabled(), "model": ai.model() if ai.enabled() else None,
                          "per_day": ai.per_day(), "today": st.get("count", 0) if st.get("day") == today else 0},
                "agents": self._agents_view()}

    def _agents_view(self) -> dict:
        """v0.40 : mode d'agents (COACH_AGENTS) et équipe, pour Réglages › Application."""
        from ..agents.eye import EYE_COOLDOWN_DAYS, EYE_LEVEL
        from ..agents.team import roster_view
        return {**roster_view(self.pilot.deps), "eye_level": EYE_LEVEL, "eye_every_days": EYE_COOLDOWN_DAYS}

    def check_image_key(self) -> dict:
        from .. import avatar_image as ai
        ok, msg = ai.check_key()
        return {"ok": ok, "message": msg}

    def check_claude(self) -> dict:
        """v0.48 : « Tester Claude » (Réglages › Application). Un tout petit appel structuré sur chaque niveau
        (small, main) et un aller-retour d'outils, SANS repli hors ligne : l'erreur exacte remonte (clé, droits,
        modèle inconnu, requête refusée…), avec les identifiants de modèle. Coût : environ 0,1 centime."""
        from pydantic import BaseModel

        from ..llm import MODELS, _detail, _reason
        from ..sandbox import cost_eur

        class Ping(BaseModel):
            ok: bool
            message: str = ""

        class _Tool:
            name, description = "date_du_jour", "Donne la date du jour."

            def spec(self) -> dict:
                return {"name": self.name, "description": self.description,
                        "input_schema": {"type": "object", "properties": {}}}

        today = self.today().isoformat()

        class _Box:
            tools, calls, degraded = [_Tool()], [], None

            def call(self, name, args=None):
                self.calls.append({"name": name, "ms": 0.0})
                return {"date": today}

        llm = self.pilot.deps.llm
        primary = getattr(llm, "primary", llm)
        if type(primary).__name__ != "AnthropicLLM":
            return {"ok": False, "steps": [{"step": "configuration", "ok": False, "model": None,
                                             "message": "Claude n'est pas activé sur ce serveur (pas de clé "
                                                        "ANTHROPIC_API_KEY au démarrage) : le coach répond en mode "
                                                        "simplifié."}]}
        steps, n0 = [], len(primary.usage_log)
        calls = [("Sortie structurée (small)", "small", lambda: primary.structured(
                     node="claude_check", tier="small", system="Test de connexion de l'application.",
                     user="Réponds ok=true et message='bonjour'.", schema=Ping)),
                 ("Sortie structurée (main)", "main", lambda: primary.structured(
                     node="claude_check", tier="main", system="Test de connexion de l'application.",
                     user="Réponds ok=true et message='bonjour'.", schema=Ping)),
                 ("Boucle d'outils (agents)", "small", lambda: primary.tool_loop(
                     node="claude_check_tools", tier="small", system="Test : appelle l'outil date_du_jour, puis "
                     "reponse_finale avec ok=true et la date en message.", user="Quelle est la date ?",
                     tools=_Box(), schema=Ping, max_steps=3))]
        for label, tier, fn in calls:
            t0 = time.time()
            try:
                out = fn()
                res = getattr(out, "output", out)
                steps.append({"step": label, "ok": bool(getattr(res, "ok", True)), "model": MODELS[tier],
                              "ms": round((time.time() - t0) * 1000), "message": "OK"})
            except Exception as e:                               # noqa: BLE001
                log.warning("test Claude, %s (%s) : %s : %s", label, MODELS[tier], type(e).__name__, e)
                steps.append({"step": label, "ok": False, "model": MODELS[tier],
                              "ms": round((time.time() - t0) * 1000), "message": _reason(e, MODELS[tier]),
                              "detail": _detail(e, MODELS[tier], "claude_check")})
        cost = cost_eur(primary.usage_log[n0:])
        self.store.set(f"spent:{self.today().isoformat()}", self._spent() + cost)
        return {"ok": all(s["ok"] for s in steps), "steps": steps, "cost_eur": round(cost, 4)}

    def llm_name(self) -> str:
        n = type(self.pilot.deps.llm).__name__
        return "claude" if "Anthropic" in n or "Fallback" in n else "heuristique"

    def career_view(self) -> dict:
        from ..career import AVATAR_DEFAULT, Facts, career, clean_avatar, resolve_scene
        with self.lock:
            p = self.profile()
            races = ([(p.goal_name, p.goal_date, p.goal_distance_km)]
                     + [(e.name, e.date, e.distance_km) for e in p.events if e.role == "B"]) if p else []
            strength = self.store.get("strength_log") or []
            birthday = self.store.get("birthday")
            key = (self.store.fingerprint(), json.dumps([strength, self._meta(), [list(map(str, r)) for r in races]],
                                                        sort_keys=True, default=str), self.today(), birthday)
            cached = getattr(self, "_career_cache", None)
            if cached and cached[0] == key:                        # v0.31 : rien n'a changé, pas de recalcul sur 5 ans
                c = copy.deepcopy(cached[1])
            else:
                facts = Facts(self.store.activities(), self.store.all_feedback(), self.store.plan(), self._meta(),
                              strength, self.today(), races, self.pilot.settings.tz, birthday)
                c = career(facts)
                self._career_cache = (key, copy.deepcopy(c))
            self._career_xp_days = c.pop("xp_by_day", {})          # v0.50 : récap de la semaine (pas dans l'API)
            seen = self.store.get("career_seen")
            done = [t["id"] for t in c["trophies"] if t["day"]]
            first = seen is None
            seen = seen or {"ids": [], "level": 1}
            c["new"] = [] if first else [t for t in c["trophies"] if t["day"] and t["id"] not in set(seen["ids"])]
            c["level_up"] = (not first) and c["level"] > seen.get("level", 1)
            c["first_visit"] = first
            ok_scenes = {x["id"] for x in c["scenes"] if x["unlocked"]}
            c["avatar"] = clean_avatar(self.store.get("avatar") or AVATAR_DEFAULT, c["level"],
                                       {g["id"] for g in c["gear"] if g["unlocked"]}, ok_scenes)
            # v0.47 : lieu + ambiance affichés (auto remplacé) ; `scene` reste le décor unique des anciens clients
            c.update(resolve_scene(c["avatar"], c["level"], self.today(), ok_scenes))
            c["avatar_set"] = self.store.get("avatar") is not None
            c["strength_log"] = (self.store.get("strength_log") or [])[-10:][::-1]
            c["_done"] = done
            c["discipline"] = self.prefs().discipline or (p.goal_kind if p and p.goal_kind in ("route", "trail")
                                                          else None)
            c["avatar_image"] = self._avatar_image(c)
            from ..agents.eye import EYE_LEVEL
            c["eye_level"] = EYE_LEVEL                              # v0.40 : récompense de niveau (Œil du coach)
            return c

    # -- image de l'avatar (v0.25, Gemini « Nano Banana », facultatif)
    def _home(self) -> Path:
        return Path(self.store.path).parent

    def _worn(self, c: dict) -> list[str]:
        from ..career import worn_gear
        return worn_gear(c["avatar"], c["gear"])

    def _avatar_image(self, c: dict, force: bool = False) -> dict:
        from .. import avatar_image as ai
        worn = self._worn(c)
        face = self._face()
        face_sig = hashlib.sha256(face[0]).hexdigest()[:12] if face else None
        key = ai.key_for(c["avatar"], c["level"], worn, c["place"], c.get("discipline"), face_sig, c["ambiance"])
        st = self.store.get("avatar_image") or {}
        f = self._home() / "avatars" / f"avatar-{st.get('key', '')}.png"
        out = {"enabled": ai.enabled(), "ready": bool(st.get("key")) and f.exists(),
               "current": st.get("key") == key and f.exists(), "generating": bool(self.job.get("avatar")),
               "error": st.get("error"), "url": f"/api/career/avatar.png?k={st.get('key', '')}",
               "every_h": ai.every_hours(), "next_at": None, "face": face is not None}
        last = st.get("at")
        if last and out["ready"]:
            nxt = datetime.fromisoformat(last) + timedelta(hours=ai.every_hours())
            if nxt > datetime.now(UTC):
                out["next_at"] = nxt.isoformat()
        if not ai.enabled() or self.job.get("avatar") or (out["current"] and not force):
            return out
        new_face = face_sig is not None and st.get("face_sig") != face_sig
        if out["next_at"] and not new_face:                    # v0.30 : une image au plus toutes les 48 h (frais) ;
            return out                                         # v0.34 : sauf la 1re image avec une nouvelle photo
        today = self.today().isoformat()
        count = st.get("count", 0) if st.get("day") == today else 0
        if count >= ai.per_day() or self._spent() + ai.IMAGE_EUR > self.pilot.settings.daily_budget_eur:
            out["error"] = "limite d'images du jour atteinte : nouvelle image demain"
            return out
        if st.get("failed_key") == key and not force:          # déjà échoué pour cette tenue : pas de boucle
            return out
        ref = f.read_bytes() if out["ready"] else None
        text = ai.prompt(c["avatar"], c["level"], worn, c["place"], c.get("discipline"), c["ambiance"])
        self.job["avatar"] = True
        out["generating"] = True

        def run():
            try:
                png = ai.generate(text, ref, face=face)
                ai.save(self._home(), key, png)
                with self.lock:
                    self.store.set("avatar_image", {"key": key, "day": today, "count": count + 1, "error": None,
                                                    "face_sig": face_sig,
                                                    "at": datetime.now(UTC).isoformat(timespec="seconds")})
                    self.store.set(f"spent:{today}", self._spent() + ai.IMAGE_EUR)
            except Exception as e:                              # noqa: BLE001
                log.warning("image de l'avatar : %s", e)
                with self.lock:
                    self.store.set("avatar_image", {**st, "day": today, "count": count, "failed_key": key,
                                                    "error": f"image indisponible ({str(e)[:260]})"})
            finally:
                self.job.pop("avatar", None)
        if self.background:
            threading.Thread(target=run, daemon=True, name="avatar-image").start()
        else:
            run()
            st2 = self.store.get("avatar_image") or {}
            out.update(generating=False, ready=bool(st2.get("key")), current=st2.get("key") == key,
                       error=st2.get("error"), url=f"/api/career/avatar.png?k={st2.get('key', '')}")
        return out

    # -- v0.34 : photo du visage (niveau 10), gardée sur le serveur, hors sauvegardes, supprimable
    def _face_path(self) -> Path:
        return self._home() / "private" / "face.bin"

    def _face(self) -> tuple[bytes, str] | None:
        from .. import avatar_image as ai
        p = self._face_path()
        if not p.exists():
            return None
        data = p.read_bytes()
        mime = ai.face_mime(data)
        return (data, mime) if mime else None

    def set_face(self, data: bytes) -> dict:
        from .. import avatar_image as ai
        from ..career import FACE_LEVEL
        c = self.career_view()
        if c["level"] < FACE_LEVEL:
            raise PermissionError(f"la photo se débloque au niveau {FACE_LEVEL}")
        if not ai.enabled():
            raise RuntimeError("image IA non activée sur ce serveur (clé Gemini absente)")
        if not data or len(data) > ai.FACE_MAX:
            raise ValueError("photo vide ou trop lourde (5 Mo au plus)")
        if not ai.face_mime(data):
            raise ValueError("format non reconnu : JPEG, PNG, WebP ou HEIC")
        p = self._face_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(0o600)
        return self.career_view()

    def delete_face(self) -> dict:
        self._face_path().unlink(missing_ok=True)
        return self.career_view()

    def avatar_png(self) -> Path | None:
        st = self.store.get("avatar_image") or {}
        f = self._home() / "avatars" / f"avatar-{st.get('key', '')}.png"
        return f if st.get("key") and f.exists() else None

    def regenerate_avatar(self) -> dict:
        c = self.career_view()
        st = self.store.get("avatar_image") or {}
        st.pop("failed_key", None)                             # l'image actuelle reste tant que la nouvelle n'est pas prête
        self.store.set("avatar_image", st)
        return self._avatar_image(c, force=True)

    def set_birthday(self, day: int | None, month: int | None) -> dict:
        """v0.33 : jour et mois seulement (pas l'année), pour le trophée et le chapeau d'anniversaire."""
        if day is None or month is None:
            with self.lock:
                self.store.set("birthday", None)
            return self.career_view()
        date(2000, month, day)                                   # lève ValueError si la date n'existe pas
        with self.lock:
            self.store.set("birthday", f"{month:02d}-{day:02d}")
        return self.career_view()

    def career_seen(self) -> dict:
        c = self.career_view()
        with self.lock:
            self.store.set("career_seen", {"ids": c["_done"], "level": c["level"]})
        return {"ok": True}

    def set_avatar(self, x: dict) -> dict:
        from ..career import clean_avatar
        c = self.career_view()
        a = clean_avatar(x or {}, c["level"], {g["id"] for g in c["gear"] if g["unlocked"]},
                         {s["id"] for s in c["scenes"] if s["unlocked"]})
        with self.lock:
            self.store.set("avatar", a)
        return {"avatar": a}

    def add_strength(self, exercise: str, kg: float, reps: int, bodyweight: float, day: str | None = None) -> dict:
        from ..career import one_rm
        if exercise not in ("squat", "box_squat", "trap_bar", "deadlift"):
            raise ValueError("exercice inconnu")
        if not (20 <= bodyweight <= 200) or not (1 <= kg <= 500) or not (1 <= reps <= 12):
            raise ValueError("valeurs hors bornes (poids 20-200 kg, charge 1-500 kg, 1 à 12 répétitions)")
        d = day or self.today().isoformat()
        date.fromisoformat(d)
        with self.lock:
            log_ = self.store.get("strength_log") or []
            log_.append({"day": d, "exercise": exercise, "kg": float(kg), "reps": int(reps),
                         "bodyweight": float(bodyweight), "one_rm": round(one_rm(kg, reps), 1)})
            self.store.set("strength_log", log_[-200:])
        return self.career_view()
