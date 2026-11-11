"""« Œil du coach » côté application (v0.40) : déblocage par la carrière, 1 analyse / 30 jours, tâche de fond.

- `eye_status()` : verrou (niveau), prochaine disponibilité, analyse en cours, dernier rapport ;
- `eye_start()` : lance l'analyse en tâche de fond (même file que les réactions du coach : ordre des messages,
  indicateur « le coach écrit ») ; lève `EyeRefused` (→ 409) si verrouillé, trop tôt, déjà en cours ou budget atteint.
Le rapport est gardé dans la base (`eye_report`) et un court message du coach dans le chat y renvoie.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from ..agents import eye as E

log = logging.getLogger("coach.web")
HISTORY_KEY = "eye_history"
FREE_KEY = "eye_welcome_used"          # v0.41 : première analyse offerte dès l'inscription, puis niveau 7


class EyeRefused(Exception):
    def __init__(self, message: str, status: dict):
        super().__init__(message)
        self.message, self.status = message, status


class EyeFlow:
    """Mixin de CoachService."""

    def _eye_level(self) -> tuple[int, int]:
        c = self.career_view()
        return c["level"], c["xp"]

    def eye_status(self) -> dict:
        from ..career import total_for_level
        with self.lock:
            level, xp = self._eye_level()
            last = self.store.get(E.EYE_KEY)
            today = self.today()
            nxt = date.fromisoformat(last["day"]) + timedelta(days=E.EYE_COOLDOWN_DAYS) if last else None
            running = bool(getattr(self, "_eye_running", False))
            need = total_for_level(E.EYE_LEVEL)
            unlocked = level >= E.EYE_LEVEL
            # v0.41 : une analyse offerte (une seule, jamais rendue si elle a abouti) avant le niveau requis
            free = not unlocked and not self.store.get(FREE_KEY) and not last
            return {"required_level": E.EYE_LEVEL, "level": level, "unlocked": unlocked, "free_available": free and not running,
                    "xp": xp, "xp_required": need, "progress_pct": 100 if unlocked else min(99, round(100 * xp / need)),
                    "cooldown_days": E.EYE_COOLDOWN_DAYS, "running": running,
                    "next_at": nxt.isoformat() if nxt and nxt > today else None,
                    "available": (unlocked and (nxt is None or nxt <= today) or free) and not running,
                    "last": last, "history": [{"day": h["day"], "summary": (h.get("report") or {}).get("summary", h.get(
                        "summary", ""))[:300]} for h in self.store.get(HISTORY_KEY) or []]}

    def eye_start(self) -> dict:
        st = self.eye_status()
        free = st["free_available"]
        if not st["unlocked"] and not free and not st["running"]:
            raise EyeRefused(f"🔒 L'Œil du coach se débloque au niveau {E.EYE_LEVEL} (tu es au niveau {st['level']}).", st)
        if st["running"]:
            raise EyeRefused("L'analyse est déjà en cours : le coach écrit…", st)
        if st["next_at"] and not free:
            d = date.fromisoformat(st["next_at"])
            raise EyeRefused(f"Une analyse tous les {E.EYE_COOLDOWN_DAYS} jours : la prochaine sera possible le "
                             f"{d:%d/%m}.", st)
        budget = self.pilot.settings.daily_budget_eur
        if self._spent() >= budget:
            raise EyeRefused(f"Budget Claude du jour atteint ({budget:.2f} €) : relance l'analyse demain.", st)
        with self.lock:
            self._eye_running = True
            user = self.store.add_chat("user", "text", "👁 Lance l'Œil du coach : analyse de mes 8 dernières semaines.")

        def on_error():
            with self.lock:
                self._eye_running = False
                self.store.add_chat("coach", "reply", "Je n'ai pas pu terminer l'Œil du coach cette fois-ci. "
                                    "Ton analyse n'est pas décomptée : relance-la dans un moment.", {"fallback": True})

        self._spawn_job("eye", lambda: self._eye_run(free=free), on_error)
        return {"started": True, "typing": self.coach_async and self.typing(), "messages": [user],
                **{k: v for k, v in self.eye_status().items() if k not in ("last", "history")}}

    def _eye_run(self, free: bool = False) -> None:
        from ..agents.trace import cost_by_agent
        from ..sandbox import cost_eur
        with self.turn_lock:
            llm = self.pilot.deps.llm
            n0, f0, t0 = len(llm.usage_log), getattr(llm, "failures", 0), time.time()
            try:
                res = E.run_eye(self.pilot.deps, self.thread)
            finally:
                self._eye_running = False
            with self.lock:
                used = llm.usage_log[n0:]
                cost = cost_eur(used)
                self.store.set(f"spent:{self.today().isoformat()}", self._spent() + cost)
                costs = cost_by_agent(used)
                agents = [{"name": n["name"], "label": n["label"], "icon": n["icon"], "tools": n["tools"],
                           "risks": n.get("risks", [])[:3], "risk_level": n["risk_level"], "veto": n["veto"], "evidence_ids": n["evidence_ids"],
                           "findings": n["findings"][:4], "cost_eur": costs.get(n["name"], 0.0), "ms": n["ms"]}
                          for n in res["notes"]]
                crit = res["critic"] or {}
                critic = {"verdict": crit.get("verdict", "ok"), "reasons": crit.get("reasons", [])[:4],
                          "revised": bool(crit.get("revised")), "rounds": crit.get("rounds", 1)}
                degraded = getattr(llm, "degraded", None) if getattr(llm, "failures", 0) > f0 else None
                rec = {"at": datetime.now().isoformat(timespec="seconds"), "day": self.today().isoformat(),
                       "report": res["report"], "agents": agents, "critic": critic, "cost_eur": round(cost, 4),
                       "latency_s": round(time.time() - t0, 2), "degraded": degraded}
                if free:
                    rec["free"] = True
                    self.store.set(FREE_KEY, rec["day"])          # l'analyse offerte est consommée (si elle a abouti)
                self.store.set(E.EYE_KEY, rec)
                hist = (self.store.get(HISTORY_KEY) or [])[-11:]          # historique : 12 rapports complets
                self.store.set(HISTORY_KEY, hist + [rec])
                rep = res["report"]
                prios = " ".join(f"{i + 1}) {p['action'].rstrip('.')}." for i, p in enumerate(rep["priorities"]))
                text = ("👁 Ton Œil du coach est prêt : toute l'équipe a analysé tes 8 dernières semaines. "
                        + (f"Mes 3 priorités : {prios} " if prios else "")
                        + "Le rapport complet est dans Progrès."
                        + (f" C'était ton analyse offerte : la suivante se débloque au niveau {E.EYE_LEVEL}, "
                           "chaque séance validée t'en rapproche." if free and not self._eye_level()[0] >= E.EYE_LEVEL else ""))
                self.store.add_chat("coach", "reply", text, {
                    "eye": True, "trace": True, "cost_eur": round(cost, 4), "latency_s": rec["latency_s"],
                    "nodes": [n for n, _ in res["trace"]], "agents": agents, "critic": critic,
                    "routing": res["routing"], "cost_by_agent": costs, **({"degraded": degraded} if degraded else {})})


__all__ = ["EyeFlow", "EyeRefused"]
