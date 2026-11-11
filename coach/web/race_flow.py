"""v0.38 — Simulateur de course de l'objectif principal (vue « #course ») : simulation, GPX privé, ravitos saisis.

- `race_sim(heat, form, target)` : temps de passage (3 scénarios), allures par tronçon, barrières, plan nutrition ;
- `upload_race_gpx(data)` : le GPX de l'athlète (Trace de Trail = usage personnel) est gardé dans son dossier privé
  (`private/`, à côté de la photo du visage : hors git, hors sauvegardes, chmod 600), validé contre les chiffres
  officiels (±8 % km, ±15 % D+ ; au-delà, avertissement visible) ;
- `set_race_points(points)` : ravitos saisis à la main pour une course sans fichier de course.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import date, datetime
from pathlib import Path

from .. import race_nutrition as rn
from .. import race_sim as rs

log = logging.getLogger("coach.web")
GPX_KEY, POINTS_KEY, FORECAST_KEY = "race_gpx", "race_points", "race_forecast"
GPX_MAX = 15 * 1024 * 1024
_TRACK_CACHE: dict[str, tuple[float, object]] = {}


class RaceFlow:
    """Mixin de CoachService : simulateur de course."""

    # -- objectif principal
    def _main_goal(self) -> dict | None:
        g = next((x for x in self.goals(with_estimate=False) if x["role"] == "A"), None)
        if not g:
            return None
        return {**g, "day": date.fromisoformat(g["date"]) if g.get("date") else None}

    def _race_meta(self, g: dict, info: dict | None = None) -> dict | None:
        meta = rs.find_course(g["name"], g["day"])
        if meta:
            return meta
        km = g.get("km") or (info or {}).get("km")
        if not km:
            return None
        if not g.get("dplus") and info:
            g = {**g, "dplus": info.get("dplus")}
        pts = self.store.get(POINTS_KEY) or {}
        mine = pts.get("points") if pts.get("key") == rs.goal_key(g["name"], g["day"]) else []
        return rs.generic_course(g["name"], g["day"], float(km), g.get("dplus"), mine)

    def _gpx_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._home() / "private" / f"race-{h}.gpx"

    def _gpx_info(self, g: dict) -> dict | None:
        info = self.store.get(GPX_KEY) or {}
        key = rs.goal_key(g["name"], g["day"])
        if info.get("key") != key or not self._gpx_path(key).exists():
            return None
        return info

    def _track(self, path: Path):
        from ..pacer.course import load_course_gpx
        mt = path.stat().st_mtime
        hit = _TRACK_CACHE.get(str(path))
        if hit and hit[0] == mt:
            return hit[1]
        tr = load_course_gpx(path)
        _TRACK_CACHE[str(path)] = (mt, tr)
        return tr

    def _progress_estimate(self, g: dict) -> tuple[int, int, int] | None:
        from ..diagnosis import is_run
        from ..progress import estimates_at, goal_time
        try:
            est = estimates_at([a for a in self.store.activities() if is_run(a)], self.today())
            return goal_time(est, g.get("km"), g.get("dplus"), g.get("kind") or "trail") if est else None
        except Exception:                                        # noqa: BLE001
            log.exception("estimation de l'objectif")
            return None

    # -- vue
    def race_sim(self, heat: str = "normale", form: float = 0.0, target=None) -> dict:
        with self.lock:
            return self.race_sim_nolock(heat, form, target)

    def race_sim_nolock(self, heat: str = "normale", form: float = 0.0, target=None) -> dict:
        """v0.40 : sans le verrou du service (outil `race_simulation` des spécialistes, qui tournent dans les fils
        du fan-out pendant que la requête tient le verrou) ; ne fait que lire la base."""
        target_s = rs.parse_target(target)
        g = self._main_goal()
        if not g:
            return {"available": False, "reason": "Pas encore d'objectif principal : donne-le au coach."}
        goal = {"name": g["name"], "date": g["date"], "km": g.get("km"), "dplus": g.get("dplus"),
                "days_left": g.get("days_left"), "done": g.get("done")}
        info = self._gpx_info(g)
        meta = self._race_meta(g, info)
        if meta is None:
            return {"available": False, "goal": goal, "need": "distance",
                    "reason": "Je ne connais pas la distance de ton objectif : donne-la au coach, ou dépose le GPX."}
        track, gpx_err = None, None
        if info:
            try:
                track = self._track(self._gpx_path(info["key"]))
            except Exception as e:                           # noqa: BLE001 : fichier abîmé -> profil approximatif
                gpx_err = f"GPX illisible ({str(e)[:120]}) : profil approximatif."
        course = rs.build_course(meta, track)
        fc = self.store.get(FORECAST_KEY)
        anchor = rs.anchors(meta, fc if isinstance(fc, dict) else None,
                            None if meta.get("forecast_default") else self._progress_estimate(g))
        if not anchor:
            return {"available": False, "goal": goal, "need": "reference",
                    "reason": "Pas encore de référence de vitesse pour estimer ton temps (un effort plat de 5 km "
                              "ou plus suffit)."}
        sim = rs.simulate(course, anchor, heat=heat, form=form, target_s=target_s, curve=rs.athlete_curve())
        legs = rs.nutrition_legs(sim)
        from .. import nutrition as nu
        plan = rn.race_plan(legs, sim["headline"]["finish_s"] / 3600, self.store.get(nu.LOGS_KEY) or [],
                            sim["conditions"]["heat"], meta)
        for k in ("_runs", "_bounds", "_stops"):
            sim.pop(k, None)
        approx = sim["mode"] == "approx"
        return {
            "available": True, "goal": goal, **sim,
            "mode_note": ("Profil approximatif — ajoute le GPX pour un calcul précis." if approx
                          else "Calculé sur ton GPX : altitudes et pentes du tracé."),
            "gpx_error": gpx_err,
            "course": {"name": meta["race"], "official_km": meta.get("official_km"), "official_dplus": meta.get("official_dplus"),
                       "start_local": meta["start_local"], "start_place": meta.get("start_place", ""),
                       "time_limit_h": meta.get("time_limit_h"), "time_limit_note": meta.get("time_limit_note", ""),
                       "cutoff_note": meta.get("cutoff_note", ""), "altitude_note": meta.get("altitude_note", "") if approx else "",
                       "track_url": meta.get("track_url"), "track_note": meta.get("track_note", ""),
                       "sources": meta.get("sources", []), "generic": bool(meta.get("generic")),
                       "start_note": meta.get("start_note", ""), "terrain": meta.get("terrain"),
                       "aid_food": (meta.get("aid_food") or {}).get("label", ""), "mandatory": meta.get("mandatory", []),
                       "manual_points": [{"name": p["name"], "km": p["km"]} for p in meta["points"]
                                         if meta.get("generic") and 0 < p["km"] < meta["official_km"]]},
            "gpx": info and {k: info.get(k) for k in ("uploaded", "km", "dplus", "warning", "issues", "name")},
            "anchor": {k: anchor.get(k) for k in ("source", "lo_s", "median_s", "hi_s", "itra_effective", "itra_range",
                                                   "itra_equiv", "k_h")},
            "nutrition": plan,
        }

    # -- GPX privé
    def upload_race_gpx(self, data: bytes, name: str = "trace.gpx") -> dict:
        from ..pacer.course import load_course_gpx
        if not data or len(data) > GPX_MAX:
            raise ValueError("GPX vide ou trop gros (15 Mo au plus)")
        head = data[:2000].lstrip().lower()
        if b"<gpx" not in head and b"<?xml" not in head:
            raise ValueError("ce n'est pas un fichier GPX")
        with self.lock:
            g = self._main_goal()
            if not g:
                raise LookupError("pas d'objectif principal")
            meta = self._race_meta(g) or rs.generic_course(g["name"], g["day"], 0, None, [])
            key = rs.goal_key(g["name"], g["day"])
            path = self._gpx_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".gpx", delete=False) as f:
                f.write(data)
                tmp = Path(f.name)
            try:
                tmp.chmod(0o600)
                try:
                    track = load_course_gpx(tmp)
                except Exception as e:                           # noqa: BLE001
                    raise ValueError(f"GPX illisible : {str(e)[:160]}") from e
                if len(track.dist) < 20 or track.dist[-1] < 1000:
                    raise ValueError("tracé trop court (moins de 1 km ou de 20 points)")
                alt = track.alt
                if alt is None or (alt != alt).all():
                    raise ValueError("le GPX n'a pas d'altitudes : exporte-le avec le profil")
                if meta.get("official_km"):
                    off = track.dist[-1] / 1000 / float(meta["official_km"]) - 1
                    if abs(off) > 0.30:
                        raise ValueError(f"{track.dist[-1] / 1000:.1f} km pour {meta['official_km']:g} km officiels : "
                                         "ce n'est sans doute pas le bon tracé")
                rep = rs.check_gpx(track, meta) if meta.get("official_km") else rs.check_gpx(track, {})
                tmp.replace(path)
                path.chmod(0o600)
            finally:
                tmp.unlink(missing_ok=True)
            info = {"key": key, "name": Path(name).name[:80] or "trace.gpx", "uploaded": datetime.now().isoformat(timespec="seconds"),
                    "km": rep["km"], "dplus": rep["dplus"], "issues": rep["issues"], "warning": rep["warning"]}
            self.store.set(GPX_KEY, info)
            _TRACK_CACHE.pop(str(path), None)
        return {"ok": True, "gpx": {k: info[k] for k in ("uploaded", "km", "dplus", "warning", "issues", "name")}}

    def delete_race_gpx(self) -> dict:
        with self.lock:
            info = self.store.get(GPX_KEY) or {}
            if info.get("key"):
                self._gpx_path(info["key"]).unlink(missing_ok=True)
            self.store.set(GPX_KEY, None)
        return {"ok": True}

    def set_race_points(self, points: list[dict]) -> dict:
        """Ravitos d'une course sans fichier : [{name, km}] (au plus 30)."""
        with self.lock:
            g = self._main_goal()
            if not g:
                raise LookupError("pas d'objectif principal")
            if rs.find_course(g["name"], g["day"]):
                raise ValueError("les ravitos de cette course sont déjà connus")
            km = float(g.get("km") or 1000)
            clean = []
            for p in points[:30]:
                k = float(p.get("km") or 0)
                if 0 < k < km:
                    clean.append({"name": str(p.get("name") or "").strip()[:60] or f"Ravito km {k:g}", "km": round(k, 2)})
            clean.sort(key=lambda p: p["km"])
            self.store.set(POINTS_KEY, {"key": rs.goal_key(g["name"], g["day"]), "points": clean})
        return {"ok": True, "points": clean}
