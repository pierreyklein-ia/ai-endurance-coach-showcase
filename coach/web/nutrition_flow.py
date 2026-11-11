"""v0.38 — Bilan nutrition des sorties ≥ 1 h 30 (fiche de retour de séance), conseils et réponse du coach.

Le bilan est enregistré sous la clé `nutrition_logs` (forme décrite dans `coach/nutrition.py`, lue aussi
par le plan nutrition de course). L'athlète voit une ligne de résumé dans le chat ; le coach reçoit en
contexte caché les pistes calculées par règles et répond en tâche de fond (même mécanisme que les
déplacements). Si ça s'est mal passé, les pistes sont ajoutées à sa réponse : elle contient toujours des
propositions concrètes.
"""
from __future__ import annotations

from datetime import date

from .. import nutrition as nu
from ..nutrition_foods import BY_ID, FAMILY_LABELS, catalog, target_carbs_g_h


class NutritionFlow:
    """Mixin de CoachService : bilan nutrition."""

    def _longest_activity(self, day: date):
        acts = [a for a in self.store.activities(since=day) if a.local_day == day]
        return max(acts, key=lambda a: a.duration_s) if acts else None

    def _with_nutrition(self, sessions: list[dict], start: date, end: date) -> None:
        """Ajoute à chaque séance : durée réelle (sortie du jour la plus longue), éligibilité au bilan
        nutrition (≥ 1 h 30 réelle, sinon prévue) et bilan déjà saisi."""
        acts: dict[str, int] = {}
        for a in self.store.activities(since=start):
            if a.local_day <= end:
                k = a.local_day.isoformat()
                acts[k] = max(acts.get(k, 0), round(a.duration_s / 60))
        logs = {x.get("session_id"): x for x in self.store.get(nu.LOGS_KEY) or [] if x.get("session_id")}
        for s in sessions:
            real = acts.get(s["day"])
            s["actual_min"] = real
            dur = real or s["duration_min"] or 0
            s["nutrition_eligible"] = bool(dur >= nu.MIN_DURATION_MIN and s["intensity"] != "rest")
            s["nutrition"] = logs.get(s["session_id"])

    def nutrition_foods(self) -> dict:
        trained = nu.summary(self.store.get(nu.LOGS_KEY)).get("trained_max_g_h")
        return {"foods": catalog(), "families": [{"id": k, "label": v} for k, v in FAMILY_LABELS.items()],
                "symptoms": [{"id": k, "label": v} for k, v in nu.SYMPTOMS.items()],
                "digestion": [{"v": k, "label": v} for k, v in nu.DIGESTION.items()],
                "trained_max_g_h": trained, "min_duration_min": nu.MIN_DURATION_MIN}

    def nutrition_summary(self) -> dict:
        s = nu.summary(self.store.get(nu.LOGS_KEY))
        lo, hi = target_carbs_g_h(3.0, s["trained_max_g_h"])
        names = {i: BY_ID[i].name for i in s["tolerated"] + s["avoid"] if i in BY_ID}
        return {**s, "target_long_g_h": [lo, hi], "labels": names,
                "symptom_labels": dict(nu.SYMPTOMS)}

    def nutrition_view(self) -> dict:
        with self.lock:
            logs = self.store.get(nu.LOGS_KEY) or []
            return {"logs": logs, "summary": self.nutrition_summary()}

    def save_nutrition(self, *, session_id: str | None, items: list[dict], water_ml: float, digestion: int,
                       symptoms: list[str], comment: str = "", duration_min: float | None = None,
                       day: date | None = None, rpe: int | None = None, note: str = "") -> dict:
        with self.lock:
            s = None
            if session_id:
                s = next((x for x in self.store.plan() if x.session_id == session_id), None)
                if s is None:
                    raise KeyError("séance inconnue")
                day = s.day
            if day is None:
                raise ValueError("séance ou date requise")
            act = self._longest_activity(day)
            dur = duration_min or (round(act.duration_s / 60) if act else None) or (s.duration_min if s else None)
            if not dur:
                raise ValueError("durée inconnue")
            log = nu.build_log(date=day.isoformat(), session_id=session_id, activity_id=act.key if act else None,
                               duration_h=dur / 60, items=items, water_ml=water_ml, digestion=digestion,
                               symptoms=symptoms, comment=comment)
            logs = [x for x in self.store.get(nu.LOGS_KEY) or []
                    if not (session_id and x.get("session_id") == session_id)]      # une saisie par séance
            history = list(logs)
            logs.append(log)
            logs.sort(key=lambda x: x.get("date") or "")
            self.store.set(nu.LOGS_KEY, logs)
            tips = nu.advice(log, history)

            label = "Sortie longue" if (s is None or s.intensity == "long") else self._title(s)
            line = nu.athlete_line(log, label)
            if rpe:
                line += f" RPE {rpe}/10."
            if (note or "").strip():
                line += f" {note.strip()[:500]}"
            user = self.store.add_chat("user", "text", line)
            bullets = "\n".join("• " + t for t in tips)
            graph_pending = self._graph_pending()
            pending = graph_pending or self.pending()
            messages = [user]
            if pending:                                       # pas de graphe : le bilan serait pris pour la réponse
                body = ("Merci pour ce bilan." + (f" Pistes pour la prochaine sortie longue :\n{bullets}" if tips else "")
                        + ("\n\nMa proposition de tout à l'heure reste en attente : réponds oui ou non." if graph_pending
                           else ""))
                messages.append(self.store.add_chat("coach", "ask" if graph_pending else "reply", body,
                                                    {"nutrition": True, "deterministic": True}))
            else:
                bad = nu.needs_proposals(log)
                context = ("[Bilan nutrition saisi par l'athlète dans l'application après une sortie de "
                           f"{nu.fmt_duration(log['duration_h'])}. Repères calculés par règles : "
                           + (" ; ".join(tips) if tips else "rien de particulier")
                           + (". Ça s'est mal passé : fais des propositions concrètes (quoi changer, combien, quand) ; "
                              "la liste des pistes sera ajoutée à la fin de ton message, ne la recopie pas, priorise-la."
                              if bad else ". Réponds brièvement, encourage ce qui a marché.")
                           + " Consigne : 3-4 phrases, ne modifie pas le plan d'entraînement pour ce bilan.]")

                def post(body: str) -> str:
                    return body + (f"\n\nPistes pour la prochaine sortie longue :\n{bullets}" if bad and tips else "")

                def fallback(_why: str = "") -> str:
                    return "Merci pour ce bilan nutrition." + (f" Pistes pour la prochaine sortie longue :\n{bullets}"
                                                               if tips else "")

                # un bilan nutrition ne doit pas déclencher d'allègement automatique du plan (repli des garde-fous)
                self._spawn_coach(user, line, context, fallback, what="nutrition", post=post, tolerate={"*"})
            return {"log": log, "advice": tips, "summary": nu.summary(logs), "messages": messages,
                    "pending": pending, "typing": self.typing()}
