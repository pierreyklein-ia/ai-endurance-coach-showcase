"""Port de données des spécialistes (v0.40) : ce que leurs outils lisent au-delà du contexte du graphe.

- `NullData` : sans base (tests, bac à sable, évaluation) ; valeurs fournies au constructeur, sinon vide ;
- `StoreData` : la base SQLite du pilote / de l'application (activités, retours, bilans nutrition, objectif).

Les outils tournent dans les fils parallèles du fan-out LangGraph : ils ne prennent JAMAIS le verrou du service
(tenu par la requête qui attend le graphe) ; les lectures SQLite sont sérialisées par `_IO`.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, timedelta

_IO = threading.Lock()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class NullData:
    """Données en mémoire (déterministes). `goal` : {name, date, km, dplus, kind}."""

    def __init__(self, today_fn: Callable[[], date] = date.today, *, goal: dict | None = None,
                 nutrition_logs: list[dict] | None = None, feedback: list[dict] | None = None,
                 activities: list[dict] | None = None, phase: str = "", estimates: dict | None = None,
                 race: dict | None = None, hr_drift: list[dict] | None = None):
        self.today_fn, self.goal = today_fn, goal
        self._logs, self._fb, self._acts = nutrition_logs or [], feedback or [], activities or []
        self._phase, self._est, self._race, self._drift = phase, estimates, race, hr_drift or []

    def activities(self, days: int = 14) -> list[dict]:
        lo = (self.today_fn() - timedelta(days=days)).isoformat()
        return [a for a in self._acts if a.get("day", "") >= lo]

    def weekly_loads(self, weeks: int = 6) -> list[dict]:
        out: dict[str, dict] = {}
        for a in self.activities(weeks * 7):
            k = _week_start(date.fromisoformat(a["day"])).isoformat()
            w = out.setdefault(k, {"week": k, "load": 0.0, "km": 0.0, "hours": 0.0, "runs": 0})
            w["load"] += a.get("load", 0)
            w["km"] += a.get("km", 0)
            w["hours"] += a.get("min", 0) / 60
            w["runs"] += 1
        return [{**w, "load": round(w["load"]), "km": round(w["km"], 1), "hours": round(w["hours"], 1)}
                for w in sorted(out.values(), key=lambda x: x["week"])]

    def adherence(self, weeks: int = 8) -> list[dict]:
        return []

    def plan(self, start: date, end: date) -> list:
        """Séances prévues entre deux dates (au-delà des 7 jours du contexte) ; vide sans base."""
        return []

    def feedback(self, days: int = 14) -> list[dict]:
        lo = (self.today_fn() - timedelta(days=days)).isoformat()
        return [f for f in self._fb if str(f.get("day", "")) >= lo]

    def nutrition_logs(self) -> list[dict]:
        return list(self._logs)

    def main_goal(self) -> dict | None:
        if not self.goal:
            return None
        g = dict(self.goal)
        if g.get("date"):
            g["days_left"] = (date.fromisoformat(str(g["date"])[:10]) - self.today_fn()).days
        return g

    def phase_brief(self) -> str:
        return self._phase

    def progress_estimates(self) -> dict | None:
        return self._est

    def race_sim(self) -> dict | None:
        return self._race

    def hr_drift(self) -> list[dict]:
        return list(self._drift)


class StoreData(NullData):
    """Lit la base du pilote. `race_sim_fn` (posé par l'application) : simulateur de l'objectif principal, sans verrou."""

    def __init__(self, store, settings, today_fn: Callable[[], date] = date.today,
                 race_sim_fn: Callable[[], dict] | None = None):
        super().__init__(today_fn)
        self.store, self.settings, self.race_sim_fn = store, settings, race_sim_fn

    def activities(self, days: int = 14) -> list[dict]:
        with _IO:
            acts = self.store.activities(since=self.today_fn() - timedelta(days=days))
        return [{"day": a.local_day.isoformat(), "sport": a.sport, "min": round(a.duration_s / 60),
                 "km": round(a.distance_m / 1000, 1), "dplus": round(a.dplus_m or 0), "load": round(a.load),
                 "hr": round(a.avg_hr) if a.avg_hr and a.avg_hr == a.avg_hr else None} for a in acts]

    def adherence(self, weeks: int = 8) -> list[dict]:
        today = self.today_fn()
        start = _week_start(today) - timedelta(weeks=weeks - 1)
        with _IO:
            plan = [s for s in self.store.plan(start, today) if s.intensity != "rest" and not s.is_race
                    and not s.session_id.endswith("-bonus")]
            fb = self.store.feedback_for([s.session_id for s in plan])
            acts = self.store.activities(since=start)
        ran = {a.local_day for a in acts if a.duration_s >= 20 * 60}
        out = []
        for i in range(weeks):
            lo = start + timedelta(weeks=i)
            hi = lo + timedelta(days=6)
            wk = [s for s in plan if lo <= s.day <= min(hi, today - timedelta(days=1))]   # aujourd'hui : pas encore jugé
            if not wk:
                continue
            done = sum(1 for s in wk if (fb.get(s.session_id) or {}).get("status") in ("done", "modified")
                       or (s.session_id not in fb and s.day in ran))
            skipped = sum(1 for s in wk if (fb.get(s.session_id) or {}).get("status") == "skipped")
            out.append({"week": lo.isoformat(), "planned": len(wk), "done": done, "skipped": skipped})
        return out

    def plan(self, start: date, end: date) -> list:
        with _IO:
            return list(self.store.plan(start, end))

    def feedback(self, days: int = 14) -> list[dict]:
        lo = (self.today_fn() - timedelta(days=days)).isoformat()
        with _IO:
            fb = self.store.all_feedback()
        return [{"day": f["day"], "status": f["status"], "rpe": f["rpe"], "note": (f["note"] or "")[:160]}
                for f in fb if str(f["day"]) >= lo]

    def nutrition_logs(self) -> list[dict]:
        from ..nutrition import LOGS_KEY
        with _IO:
            return list(self.store.get(LOGS_KEY) or [])

    def _profile(self):
        from ..onboarding import Profile
        with _IO:
            p = self.store.get("profile")
        return Profile.model_validate(p) if p else None

    def main_goal(self) -> dict | None:
        from ..goals import goals_list
        p = self._profile()
        today = self.today_fn()
        s = self.settings
        gl = goals_list(p, today, None if p else s.race_name, None if p else s.race_date)
        g = next((x for x in gl if x["role"] == "A"), None)
        return {k: g.get(k) for k in ("name", "date", "days_left", "km", "dplus", "kind", "done")} if g else None

    def phase_brief(self) -> str:
        from ..race_specific import phase_brief
        with _IO:
            prof = self.store.get("profile")
        try:
            return phase_brief(prof, self.today_fn())
        except Exception:                                        # noqa: BLE001 : un outil ne casse jamais un tour
            return ""

    def progress_estimates(self) -> dict | None:
        from ..diagnosis import is_run
        from ..progress import estimates_at, goal_time
        with _IO:
            runs = [a for a in self.store.activities() if is_run(a)]
        est = estimates_at(runs, self.today_fn())
        if not est:
            return None
        g = self.main_goal()
        gt = goal_time(est, g.get("km"), g.get("dplus"), g.get("kind") or "trail") if g and g.get("km") else None
        return {"ref": est["ref"], "10 km": est["10 km"], "semi": est["semi"], "marathon": est["marathon"],
                "goal_estimate_s": list(gt) if gt else None}

    def race_sim(self) -> dict | None:
        if self.race_sim_fn is None:
            return None
        try:
            r = self.race_sim_fn()
        except Exception:                                        # noqa: BLE001
            return None
        if not r or not r.get("available"):
            return None
        n = r.get("nutrition") or {}
        cut = [{"name": p["name"], "km": p["km"], "clock": p["cutoff"]["clock"],
                "margin_min": (p["cutoff"].get("margin_min") or {}).get("prudent"), "status": p["cutoff"].get("status")}
               for p in r.get("points", []) if p.get("cutoff")]
        climbs = sorted(r.get("climbs") or [], key=lambda c: -c.get("gain_m", 0))[:4]
        return {"headline": (r.get("headline") or {}).get("text"),
                "scenarios": [{"label": s["label"], "finish_clock": s.get("finish_clock"), "finish_s": s["finish_s"]}
                              for s in r.get("scenarios", [])],
                "mode": r.get("mode"), "total_km": r.get("total_km"),
                # marges sur les barrières horaires (scénario prudent) et plus grosses montées du profil
                "cutoffs": sorted(cut, key=lambda c: c["margin_min"] if c["margin_min"] is not None else 1e9)[:4],
                "climbs": [{k: c.get(k) for k in ("name", "km0", "km1", "gain_m", "grade_pct", "time_s")} for c in climbs],
                "nutrition": {"target_g_h": n.get("target_g_h"), "range_g_h": n.get("range_g_h"),
                              "fluid_ml_h": n.get("fluid_ml_h"), "sodium_mg_h": n.get("sodium_mg_h"),
                              "rehearsal_needed": n.get("rehearsal_needed"), "notes": (n.get("notes") or [])[:3]}}

    def hr_drift(self) -> list[dict]:
        with _IO:
            dec = self.store.get("decoupling") or {}
        vals = sorted(dec.values(), key=lambda v: v.get("day", ""))[-8:]
        return [{"day": v.get("day"), "decoupling_pct": round(100 * v["dec"], 1) if v.get("dec") is not None else None}
                for v in vals]


__all__ = ["NullData", "StoreData"]
