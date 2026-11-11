"""v0.37-v0.38 — Déplacer une séance d'un jour à l'autre (glisser-déposer dans Semaine).

v0.38 : l'athlète a le dernier mot sur ses jours. Le déplacement passe toujours par les MÊMES
garde-fous déterministes que les propositions du coach (`guardrails.apply_changes` + `check`, contexte
construit comme le nœud `guard` du graphe), mais ils ne BLOQUENT plus : leurs violations deviennent des
avertissements (on précise lesquelles auraient bloqué une proposition du coach), donnés au coach comme
contexte caché et résumés dans le toast. Le coach poste TOUJOURS un message après un déplacement.

- refus (409), seulement ce qui n'a pas de sens : déplacer une course, un repos, une séance passée ou
  déjà validée, ou déplacer vers le passé ;
- avertissement : nouvelle violation des garde-fous (bloquante ou non), deux séances (dures) le même
  jour, deux séances dures d'affilée, séance dure à J-1 / J-2 d'une course ; avertissement FORT : jour
  de course.

Réponse rapide (v0.38, bug « rien dans le chat ») : la requête applique le déplacement, écrit la ligne
de l'athlète dans le chat et rend la main ; la réaction du coach (LLM, 20-60 s en production) tourne en
tâche de fond (`CoachService._spawn_coach`) ; `GET /api/chat` répond `typing: true` pendant ce temps.

Question oui/non en attente : le déplacement est fait, la proposition du coach reste ouverte et le
texte n'est PAS envoyé au graphe (il serait pris pour la réponse). Message déterministe, et promesse
tenue : si l'athlète dit oui, chaque séance qu'il a déplacée depuis garde son jour (`moved_since_pending`,
appliqué par `rebase_changes` au moment où l'agenda écrit la proposition confirmée).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..graph import RULE_LABELS
from ..guardrails import HARD_INTENSITIES, PlanContext, apply_changes, check
from ..onboarding import JOURS
from ..sandbox import LABELS
from ..schemas import PlanChange, PlannedSession, Signals

log = logging.getLogger("coach.web")
WARN_LABELS = {
    "R03_acwr_under": "charge de la semaine un peu basse", "R03_acwr_high": "charge élevée (bloc de choc)",
    "R06_taper_over": "affûtage trop marqué", "R06_taper_intensity": "plus de séance de qualité avant la course",
    "R06_taper_frequency": "moins de séances que d'habitude avant la course",
    "R09_peak_session": "sortie longue par rapport au dernier mois",
}
MOVED_KEY = "moved_since_pending"      # {session_id: "YYYY-MM-DD"} déplacées pendant une question oui/non


class MoveRefused(Exception):
    """Déplacement refusé : message en français pour l'athlète (HTTP 409)."""

    def __init__(self, message: str, rules: list[str] | None = None):
        super().__init__(message)
        self.message, self.rules = message, rules or []


def day_fr(d: date) -> str:
    """« mardi 3/11 »."""
    return f"{JOURS[d.weekday()]} {d.day}/{d.month}"


def is_hard(s: PlannedSession) -> bool:
    """Séance dure (même définition que les garde-fous), hors course et séance sans charge."""
    return s.intensity in HARD_INTENSITIES and not s.is_race and s.planned_load > 0


def adjacency_warnings(plan: list[PlannedSession], moved: PlannedSession, title_of) -> list[str]:
    """Avertissements de bon sens sur le plan APRÈS déplacement, autour du jour d'arrivée."""
    out: list[str] = []
    day = moved.day
    others = [s for s in plan if s.session_id != moved.session_id and s.intensity != "rest"]
    same = [s for s in others if s.day == day and not s.is_race]
    hard_same = [s for s in same if is_hard(s)]
    if is_hard(moved) and hard_same:
        out.append(f"deux séances dures le {day_fr(day)} ({title_of(moved)} et {title_of(hard_same[0])})")
    elif same:
        out.append(f"deux séances le {day_fr(day)} ({title_of(moved)} et {title_of(same[0])})")
    if is_hard(moved):
        for d in (day - timedelta(days=1), day + timedelta(days=1)):
            n = next((s for s in others if s.day == d and is_hard(s)), None)
            if n:
                a, b = sorted([moved, n], key=lambda x: x.day)
                out.append(f"deux séances dures d'affilée ({title_of(a)} le {day_fr(a.day)}, "
                           f"{title_of(b)} le {day_fr(b.day)})")
        race = next((s for s in others if s.is_race and 0 < (s.day - day).days <= 2), None)
        if race:
            out.append(f"séance dure à J-{(race.day - day).days} de la course")
    return out


def rebase_changes(changes: list[PlanChange], moved: dict[str, str]) -> list[PlanChange]:
    """Proposition confirmée après des déplacements de l'athlète : chaque séance déplacée garde le jour
    choisi par l'athlète (sauf si la proposition l'annule)."""
    out = []
    for ch in changes:
        d = moved.get(ch.session.session_id)
        if d and ch.action != "cancel" and ch.session.day.isoformat() != d:
            ch = ch.model_copy(update={"session": ch.session.model_copy(update={"day": date.fromisoformat(d)})})
        out.append(ch)
    return out


def two_hard_alert(sessions: list[PlannedSession]) -> dict | None:
    """Alerte d'un jour de Semaine : deux séances dures (ou plus) le même jour."""
    hard = [s for s in sessions if is_hard(s)]
    if len(hard) < 2:
        return None
    return {"kind": "two_hard", "label": "2 séances dures le même jour", "sessions": [s.session_id for s in hard]}


class MoveFlow:
    """Mixin de CoachService : `move_session`."""

    def _title(self, s: PlannedSession) -> str:
        return self._meta().get(s.session_id, {}).get("title") or LABELS.get(s.intensity, s.intensity)

    def _move_context(self) -> tuple[list[PlannedSession], PlanContext]:
        """Contexte identique au nœud `guard` du graphe (coach/graph.py)."""
        ctx = self.pilot.repo.get(self.thread)
        return ctx.plan_next_7d, PlanContext(
            state=ctx.load, baseline_week_load=ctx.baseline_week_load, today=self.today(),
            race_date=ctx.race_date, shock_block_active=ctx.shock_block_active,
            pre_taper_week_load=ctx.pre_taper_week_load,
            pre_taper_sessions_per_week=ctx.pre_taper_sessions_per_week,
            longest_run_30d_min=ctx.longest_run_30d_min, week_max=getattr(ctx, "week_max", None))

    def _move_alerts(self, plan, s: PlannedSession, moved: PlannedSession, day: date, changes) -> list[dict]:
        """Avertissements (jamais de refus) : [{"text", "level": warn|strong, "would_block"}]."""
        alerts: list[dict] = []
        if any(x.is_race and x.day == day for x in plan):
            alerts.append({"text": f"le {day_fr(day)} est un jour de course : une autre séance ce jour-là "
                                   "entame ta fraîcheur pour la course", "level": "strong", "would_block": False})
        # garde-fous : même plan avant / après (la séance et les repos échangés y figurent dans les deux)
        window, pctx = self._move_context()
        before = apply_changes(window, [PlanChange(action="add", session=x, reason="")
                                        for x in [s] + [r for r in plan if r.day == day and r.intensity == "rest"]])
        after = apply_changes(before, changes)
        v0, v1 = check(before, Signals(), pctx), check(after, Signals(), pctx, plan_before=before)
        old_rules = {v.rule_id for v in v0.violations}
        seen: set[str] = set()
        for v in v1.violations:
            if v.rule_id in old_rules or v.rule_id in seen:
                continue
            seen.add(v.rule_id)
            block = v.severity == "block"
            label = (RULE_LABELS if block else WARN_LABELS).get(v.rule_id) or RULE_LABELS.get(v.rule_id) \
                or WARN_LABELS.get(v.rule_id, v.rule_id)
            alerts.append({"text": label, "level": "strong" if block else "warn", "would_block": block,
                           "rule": v.rule_id})
        full_after = apply_changes(plan, changes)
        adj = [{"text": t, "level": "strong" if t.startswith("deux séances dures le") else "warn",
                "would_block": False} for t in adjacency_warnings(full_after, moved, self._title)]
        return adj + alerts

    def move_session(self, session_id: str, day: date) -> dict:
        with self.lock:
            today = self.today()
            plan = self.store.plan()
            s = next((x for x in plan if x.session_id == session_id), None)
            if s is None:
                raise KeyError("séance inconnue")
            title = self._title(s)
            if s.day == day:                                      # idempotent : rien à faire
                return {"moved": False, "session": self.session_view(s, self._meta(), {}), "warnings": [],
                        "alerts": [], "messages": [], "pending": self.pending(), "typing": self.typing(),
                        "calendar_changed": False}
            if s.is_race or s.intensity == "race":
                raise MoveRefused("Une course ne se déplace pas : sa date est celle de l'organisation.")
            renfo = self._meta().get(s.session_id, {}).get("kind") in ("renfo", "mobilite")
            if s.intensity == "rest" and not renfo:               # v0.44 : le renfo se déplace, pas le repos
                raise MoveRefused("Un jour de repos ne se déplace pas : déplace plutôt la séance.")
            # v0.45 : une séance passée NON validée (manquée) peut être replanifiée vers aujourd'hui ou plus tard ;
            # le coach commente (pas de rattrapage : avertissement R05 éventuel, jamais de blocage)
            if self.store.feedback_for([s.session_id]).get(s.session_id):
                raise MoveRefused("Cette séance est déjà validée : elle ne se déplace plus.")
            if day < today:
                raise MoveRefused("On ne déplace pas une séance dans le passé.")

            moved = s.model_copy(update={"day": day})
            # un jour de repos explicite à l'arrivée prend la place laissée libre (échange)
            meta = self._meta()
            swaps = [] if renfo else [r.model_copy(update={"day": s.day}) for r in plan
                                      if r.day == day and r.intensity == "rest" and not r.is_race
                                      and meta.get(r.session_id, {}).get("kind") not in ("renfo", "mobilite")]
            changes = [PlanChange(action="move", session=moved, reason="déplacée par l'athlète")]
            changes += [PlanChange(action="move", session=r, reason="échange avec la séance déplacée") for r in swaps]
            alerts = self._move_alerts(plan, s, moved, day, changes)
            warnings = [a["text"] + (" (aurait bloqué une proposition du coach)" if a["would_block"] else "")
                        for a in alerts]

            # écriture : même adaptateur que le nœud `calendar` du graphe (Google Agenda puis plan local)
            try:
                ops = self.pilot.deps.calendar.apply(self.thread, changes)
            except Exception as e:
                log.exception("déplacement : agenda")
                raise RuntimeError(f"Agenda indisponible, séance non déplacée : {str(e)[:160]}") from e
            base = dict(self.store.get("baseline_loads") or {})
            if s.day.isoformat() in base:                         # le plan accepté suit le déplacement (R05)
                base[s.day.isoformat()] = max(0.0, round(base[s.day.isoformat()] - s.planned_load, 1))
                base[day.isoformat()] = round(base.get(day.isoformat(), 0.0) + s.planned_load, 1)
                self.store.set("baseline_loads", base)

            graph_pending = self._graph_pending()
            pending = graph_pending or self.pending()
            if graph_pending:                                     # promesse : « si tu dis oui, je garde ton déplacement »
                mv = dict(self.store.get(MOVED_KEY) or {})
                for ch in changes:
                    mv[ch.session.session_id] = ch.session.day.isoformat()
                self.store.set(MOVED_KEY, mv)

            text = f"J'ai déplacé « {title} » de {day_fr(s.day)} à {day_fr(day)}."
            user = self.store.add_chat("user", "text", text)
            watch = (" À surveiller : " + " ; ".join(warnings) + ".") if warnings else ""
            messages = [user]
            if pending:
                # pas de passage par le graphe : le texte serait pris pour la réponse à la question en attente
                body = ("C'est noté, j'ai mis à jour ton agenda. Ma proposition de tout à l'heure reste en attente : "
                        "réponds oui ou non. Si tu dis oui, je garde ton déplacement." if graph_pending else
                        "C'est noté, j'ai mis à jour ton agenda. Ma question de tout à l'heure reste en attente.") + watch
                messages.append(self.store.add_chat("coach", "ask" if graph_pending else "reply", body,
                                                    {"move": True, "deterministic": True}))
            else:
                why = [a["text"] for a in alerts if a["would_block"]]
                recent = self.store.chat(12)
                last_coach = max((i for i, m in enumerate(recent) if m["role"] == "coach"), default=-1)
                burst = sum(1 for m in recent[last_coach + 1:] if m["role"] == "user"
                            and m["text"].startswith("J'ai déplacé"))
                context = (("[Séance MANQUÉE (prévue le " + f"{s.day:%d/%m}" + ", non faite) replanifiée par l'athlète. "
                            "Dis-lui si c'est raisonnable vu la semaine (pas de rattrapage qui empile les séances "
                            "dures), sinon propose mieux.] ") if s.day < today else "") + (
                           (f"[{burst} déplacements d'affilée (voir recent_chat) : réagis UNE fois à l'ensemble, sur le "
                            "plan final (plan_next_7d), pas sur un état intermédiaire.] " if burst > 1 else "")
                           + "[Déplacement fait par l'athlète dans l'application, déjà enregistré dans son plan et "
                           "son agenda : c'est son choix, il a le dernier mot sur ses jours"
                           + ("" if not warnings else " ; à surveiller : " + " ; ".join(warnings))
                           + (". Ces points auraient bloqué une de TES propositions : " + " ; ".join(why) if why else "")
                           + ". Consigne : réagis brièvement (2-3 phrases), signale les vrais risques s'il y en a, et ne "
                           "propose un ajustement (avec la confirmation oui/non habituelle) que s'il est vraiment "
                           "justifié ; sinon aucun changement de plan.]")

                def fallback(_why: str = "") -> str:
                    return "C'est noté, ton plan et ton agenda sont à jour." + watch

                self._spawn_coach(user, text, context, fallback, what="move",
                                  tolerate={"*"})        # le plan de l'athlète ne force pas d'allègement
            fb = self.store.feedback_for([session_id])
            return {"moved": True, "from": s.day.isoformat(), "to": day.isoformat(),
                    "session": self.session_view(moved, self._meta(), fb), "warnings": warnings, "alerts": alerts,
                    "messages": messages, "pending": pending, "typing": self.typing(),
                    "calendar_ops": ops, "calendar_changed": True}
