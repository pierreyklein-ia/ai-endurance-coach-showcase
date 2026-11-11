"""v0.42 — Validation automatique des séances prévues à partir des sorties synchronisées (Strava, FIT).

Avant : une séance n'était « faite » que si l'athlète la validait à la main (statut, RPE, note). Une sortie
courue sans validation laissait la séance « à valider » : assiduité fausse pour le coach, XP non comptée.

Règles :
- une séance prévue (pas le repos, pas une course déjà passée) du jour J, sans retour, est rapprochée d'une
  activité du même jour : course à pied pour une séance de course, renfo pour une séance de renfo ;
- une activité ne valide qu'une seule séance (la plus proche en durée) ;
- statut « faite » si la durée réelle est entre 70 % et 135 % de la durée prévue, sinon « modifiée » ;
- v0.43 : si la sortie a ses flux (FC, distance, altitude), le type couru (footing, tempo, seuil, VMA, côtes :
  `coach/session_type.py`) est comparé au type prévu ; s'il ne correspond pas, « modifiée » ;
- rien n'est jamais marqué « sautée » automatiquement (une sortie peut arriver plus tard) ;
- la validation automatique ne met ni RPE ni note : l'athlète peut compléter son ressenti, ce qui remplace
  la validation automatique (et compte alors pour les trophées « retours au coach »).
Le détail (activité retenue) est gardé dans la clé `auto_validated` : {session_id: {...}}.

v0.44 — liens modifiables : l'athlète peut délier une séance de sa sortie (elle redevient « à valider » et ce
couple n'est plus jamais refait automatiquement) ou lier une autre sortie (± 3 jours ; si la sortie est un autre
jour, la séance y est déplacée, agenda compris). Quand le type couru ne correspond pas au type prévu, le coach
le remarque dans le chat (réaction en tâche de fond) et la séance est « modifiée ».
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..diagnosis import is_run
from ..pilot.store import PROFILES_KEY
from ..schemas import PlanChange
from ..session_meta import orig_kind
from ..session_type import VERDICT_TEXT, compare, detect

log = logging.getLogger("coach.web")
AUTO_KEY = "auto_validated"
UNLINKED_KEY = "auto_unlinked"        # v0.44 : couples « séance|sortie » déliés par l'athlète
LINK_DAYS = 3
WINDOW_DAYS = 7
DONE_RANGE = (0.70, 1.35)


def _kind(meta: dict, s) -> str:
    return (meta.get(s.session_id, {}).get("kind") or s.intensity)


def _renfo(meta: dict, s) -> bool:
    return _kind(meta, s) in ("renfo", "mobilite")


def fits(s, a, renfo: bool) -> bool:
    return (a.sport == "Renfo") if renfo else is_run(a)


def evaluate(s, a, renfo: bool, profiles: dict | None, orig: str | None = None) -> dict:
    """Statut (faite / modifiée), ratio de durée et type couru d'une sortie pour une séance.
    v0.50.1 : `orig` = type prévu au départ quand le coach a changé la séance ; si c'est la séance d'origine
    que l'athlète a faite (seuil prévu, allégé en footing, seuil couru), elle est « faite », pas « modifiée »."""
    planned = max(1, s.duration_min or 0)
    ratio = (a.duration_s / 60) / planned if s.duration_min else 1.0
    status = "done" if DONE_RANGE[0] <= ratio <= DONE_RANGE[1] else "modified"
    typ = None
    if not renfo:
        detected = detect((profiles or {}).get(a.key))
        club = (s.description or "").startswith("Séance du club")   # v0.52.2 : seuil, VMA ou côtes : c'est le club
        typ = compare("club" if club else s.intensity, detected)
        if not typ["ok"] and orig and compare(orig, detected)["verdict"] == "conforme":
            typ = {**typ, "verdict": "origine", "ok": True}
        typ["text"] = VERDICT_TEXT[typ["verdict"]]
        if not typ["ok"]:
            status = "modified"
    return {"status": status, "ratio": round(ratio, 2), "type": typ}


def match_sessions(sessions, activities, meta: dict, feedback: dict, today: date,
                   profiles: dict | None = None, linked: set[str] | None = None,
                   skip: set[str] | None = None) -> list[dict]:
    """Rapprochements à faire : [{session, activity, status, ratio, type}] (fonction pure, testée).
    v0.43 : avec un profil d'intensité, le type couru est comparé au type prévu ; s'il ne correspond pas
    (footing au lieu d'un seuil, VMA au lieu d'un footing…), la séance passe en « modifiée »."""
    out: list[dict] = []
    used: set[str] = set(linked or ())          # v0.44 : une sortie déjà liée ne valide pas une 2e séance
    skip = skip or set()
    by_day: dict[date, list] = {}
    for a in activities:
        by_day.setdefault(a.local_day, []).append(a)
    for s in sorted(sessions, key=lambda x: (x.day, -(x.duration_min or 0))):
        if s.day > today or s.session_id in feedback or s.is_race:
            continue
        kind = _kind(meta, s)
        renfo = kind in ("renfo", "mobilite")
        if s.intensity == "rest" and not renfo:
            continue
        cands = [a for a in by_day.get(s.day, []) if a.start_utc.isoformat() not in used
                 and f"{s.session_id}|{a.start_utc.isoformat()}" not in skip and fits(s, a, renfo)]
        if not cands:
            continue
        planned = max(1, s.duration_min or 0)
        a = min(cands, key=lambda x: abs(x.duration_s / 60 - planned))
        used.add(a.start_utc.isoformat())
        out.append({"session": s, "activity": a, **evaluate(s, a, renfo, profiles, orig_kind(meta, s))})
    return out


class AutoValidateFlow:
    """Mixin de CoachService."""

    # ------------------------------------------------------------ outils
    def _links(self) -> dict:
        return dict(self.store.get(AUTO_KEY) or {})

    def _link_info(self, a, ev: dict, manual: bool = False) -> dict:
        return {"activity": a.name or a.sport, "start": a.start_utc.isoformat(), "day": a.local_day.isoformat(),
                "minutes": round(a.duration_s / 60), "km": round(a.distance_m / 1000, 1),
                "ratio": ev["ratio"], "status": ev["status"], "type": ev["type"], "manual": manual}

    def _activity_by_start(self, start: str):
        day = date.fromisoformat(start[:10])
        for a in self.store.activities(since=day - timedelta(days=1)):
            if a.start_utc.isoformat() == start:
                return a
        return None

    def _type_line(self, s, info: dict) -> str:
        t = info.get("type") or {}
        if not t or t.get("verdict") in ("conforme", "non_verifie", "origine"):
            return ""
        prevu = self._title(s)
        return f"type détecté : {t['label']} au lieu de « {prevu} » ({t['text']})"

    def _tell_coach(self, lines: list[str], context: str, fallback_text: str, what: str) -> None:
        """L'athlète voit la ligne dans le chat ; le coach réagit en tâche de fond (ou par règle si une question
        oui/non attend une réponse : le graphe la prendrait pour la réponse)."""
        if self.needs_onboarding():                   # pendant l'accueil, le fil sert à la découverte
            return
        text = " ".join(lines)
        user = self.store.add_chat("user", "text", text)
        if self._graph_pending() or self.pending():
            self.store.add_chat("coach", "reply", fallback_text + " Ma question de tout à l'heure reste en attente.",
                                {"deterministic": True, "link": True})
            return
        self._spawn_coach(user, text, context, lambda _w="": fallback_text, what=what, tolerate={"*"})

    # ------------------------------------------------------------ v0.50.1 : réparation au démarrage
    def repair_session_meta(self) -> dict:
        """Idempotent. (1) Les séances transformées par le coach avant le correctif gardaient le titre / type
        d'origine (« Seuil » décrit en footing) : titre, type et badge suivent la séance réelle, l'origine est
        gardée. (2) Les validations automatiques « modifiées » pour un type qui correspond à la séance d'origine
        repassent « faites » (si l'athlète n'a rien ajouté lui-même)."""
        from ..session_meta import stale, sync
        n_meta = n_fb = 0
        with self.lock:
            meta = dict(self._meta())
            plan = self.store.plan()
            for s in plan:
                if stale(meta, s):
                    k = meta[s.session_id]["kind"]
                    n_meta += sync(meta, s.model_copy(update={"intensity": k}), s)
            if n_meta:
                self.store.set("session_meta", meta)
            links, profiles = self._links(), self.store.get(PROFILES_KEY) or {}
            by_id = {s.session_id: s for s in plan}
            changed = False
            for sid, info in links.items():
                t, s = info.get("type") or {}, by_id.get(sid)
                if s is None or t.get("ok", True) or info.get("confirmed") or not orig_kind(meta, s):
                    continue
                a = self._activity_by_start(info.get("start") or "")
                if a is None:
                    continue
                ev = evaluate(s, a, _renfo(meta, s), profiles, orig_kind(meta, s))
                if not (ev["type"] or {}).get("ok"):
                    continue
                fb = self.store.feedback_for([sid]).get(sid) or {}
                if fb and (fb.get("rpe") or (fb.get("note") or "").strip()):
                    continue
                if fb.get("status") == "modified" and ev["status"] == "done":
                    self.store.set_feedback(sid, s.day, "done", None, "")
                links[sid] = {**info, "status": ev["status"], "type": ev["type"], "ratio": ev["ratio"]}
                n_fb, changed = n_fb + 1, True
            if changed:
                self.store.set(AUTO_KEY, links)
        if n_meta or n_fb:
            log.info("v0.50.1 : %d titre(s) de séance réalignés, %d validation(s) corrigée(s)", n_meta, n_fb)
        return {"meta": n_meta, "feedback": n_fb}

    # ------------------------------------------------------------ validation automatique
    def auto_validate(self) -> list[dict]:
        """Valide les séances des 7 derniers jours qui ont une activité correspondante. Renvoie les validations."""
        today = self.today()
        start = today - timedelta(days=WINDOW_DAYS)
        with self.lock:
            plan = self.store.plan(start, today)
            if not plan:
                return []
            fb = self.store.feedback_for([s.session_id for s in plan])
            acts = [a for a in self.store.activities(since=start) if a.local_day <= today]
            auto = self._links()
            todo = match_sessions(plan, acts, self._meta(), fb, today, self.store.get(PROFILES_KEY) or {},
                                  linked={x.get("start") for x in auto.values()},
                                  skip=set(self.store.get(UNLINKED_KEY) or []))
            if not todo:
                return []
            done, odd = [], []
            for m in todo:
                s, a = m["session"], m["activity"]
                self.store.set_feedback(s.session_id, s.day, m["status"], None, "")
                info = self._link_info(a, m)
                auto[s.session_id] = info
                done.append({"session_id": s.session_id, "day": s.day.isoformat(), **info})
                why = self._type_line(s, info)
                if why:
                    odd.append(f"« {self._title(s)} » du {s.day:%d/%m} validée avec ma sortie « {info['activity']} » "
                               f"({info['minutes']} min) : {why}.")
            self.store.set(AUTO_KEY, auto)
            log.info("validation automatique : %d séance(s)", len(done))
            if odd:                                       # v0.44 : le coach remarque l'écart et le dit
                self._tell_coach(
                    ["🔗 Validation automatique :"] + odd,
                    "[Séance(s) validée(s) automatiquement par la sortie synchronisée, statut « modifiée » car le "
                    "type couru diffère du prévu. Consigne : en 2-3 phrases, dis ce qui a changé par rapport au plan "
                    "et si ça modifie quelque chose pour les prochains jours (pas de rattrapage). Ne propose un "
                    "changement de plan (avec la confirmation habituelle) que s'il est vraiment justifié.]",
                    "C'est noté : séance passée en « modifiée », le plan reste valable.", "link")
            return done

    # ------------------------------------------------------------ v0.44 : lier / délier à la main
    def link_candidates(self, session_id: str) -> dict:
        with self.lock:
            s = next((x for x in self.store.plan() if x.session_id == session_id), None)
            if s is None:
                raise KeyError("séance inconnue")
            meta, today = self._meta(), self.today()
            renfo = _renfo(meta, s)
            links = self._links()
            by_start = {v.get("start"): k for k, v in links.items()}
            titles = {k: (meta.get(k, {}).get("title") or k) for k in by_start.values()}
            profiles = self.store.get(PROFILES_KEY) or {}
            lo, hi = s.day - timedelta(days=LINK_DAYS), min(today, s.day + timedelta(days=LINK_DAYS))
            out = []
            for a in self.store.activities(since=lo):
                if a.local_day > hi or not fits(s, a, renfo):
                    continue
                ev = evaluate(s, a, renfo, profiles, orig_kind(meta, s))
                other = by_start.get(a.start_utc.isoformat())
                out.append({**self._link_info(a, ev), "linked_to": other, "linked_title": titles.get(other),
                            "current": other == session_id})
            out.sort(key=lambda x: (x["day"] != s.day.isoformat(), x["start"]), reverse=False)
            return {"session_id": session_id, "day": s.day.isoformat(), "title": self._title(s), "candidates": out}

    def unlink_session(self, session_id: str, *, quiet: bool = False) -> dict:
        with self.lock:
            links = self._links()
            info = links.pop(session_id, None)
            if info is None:
                raise KeyError("cette séance n'est liée à aucune sortie")
            self.store.set(AUTO_KEY, links)
            skip = list(self.store.get(UNLINKED_KEY) or [])
            pair = f"{session_id}|{info['start']}"
            if pair not in skip:
                self.store.set(UNLINKED_KEY, (skip + [pair])[-200:])
            self.store.delete_feedback(session_id)
            return {"unlinked": True, "session_id": session_id, "activity": info}

    def link_session(self, session_id: str, activity_start: str) -> dict:
        with self.lock:
            plan = self.store.plan()
            s = next((x for x in plan if x.session_id == session_id), None)
            if s is None:
                raise KeyError("séance inconnue")
            a = self._activity_by_start(activity_start)
            if a is None:
                raise KeyError("sortie introuvable")
            meta = self._meta()
            renfo = _renfo(meta, s)
            if not fits(s, a, renfo):
                raise ValueError("cette sortie n'est pas du même sport que la séance")
            if abs((a.local_day - s.day).days) > LINK_DAYS:
                raise ValueError(f"la sortie doit être à {LINK_DAYS} jours au plus de la séance")
            links = self._links()
            for other, inf in list(links.items()):          # la sortie était liée à une autre séance : on l'en détache
                if inf.get("start") == activity_start and other != session_id:
                    links.pop(other)
                    fb_o = self.store.feedback_for([other]).get(other)
                    if fb_o and not fb_o.get("rpe") and not (fb_o.get("note") or "").strip():
                        self.store.delete_feedback(other)
            moved_from = None
            if a.local_day != s.day:                         # la séance rejoint le jour de la sortie
                moved = s.model_copy(update={"day": a.local_day})
                try:
                    self.pilot.deps.calendar.apply(self.thread, [PlanChange(
                        action="move", session=moved, reason="liée à la sortie réellement faite ce jour-là")])
                except Exception as e:
                    raise RuntimeError(f"Agenda indisponible, séance non liée : {str(e)[:160]}") from e
                moved_from, s = s.day, moved
            ev = evaluate(s, a, renfo, self.store.get(PROFILES_KEY) or {}, orig_kind(self._meta(), s))
            prev = self.store.feedback_for([session_id]).get(session_id) or {}
            self.store.set_feedback(session_id, s.day, ev["status"], prev.get("rpe"), prev.get("note") or "")
            info = self._link_info(a, ev, manual=True)
            links[session_id] = info
            self.store.set(AUTO_KEY, links)
            skip = [x for x in (self.store.get(UNLINKED_KEY) or []) if x != f"{session_id}|{activity_start}"]
            self.store.set(UNLINKED_KEY, skip)
            why = self._type_line(s, info)
            line = (f"🔗 J'ai lié ma sortie « {info['activity']} » ({info['minutes']} min"
                    + (f", {str(info['km']).replace('.', ',')} km" if info["km"] else "") + f") à la séance « {self._title(s)} »"
                    + (f", déplacée du {moved_from:%d/%m} au {s.day:%d/%m}" if moved_from else "") + "."
                    + (f" Le type ne correspond pas exactement : {why}." if why else ""))
            self._tell_coach(
                [line],
                "[L'athlète a relié à la main une séance prévue à la sortie qu'il a réellement faite. "
                + ("Le type couru diffère du prévu : la séance est « modifiée ». Consigne : en 2-3 phrases, précise "
                   "ce qui diffère (type, durée), ce que ça change pour la semaine, sans rattrapage ; ne propose un "
                   "changement de plan que s'il est vraiment justifié.]" if why or ev["status"] == "modified" else
                   "La séance correspond au prévu. Consigne : réponds en 1-2 phrases.]"),
                "C'est noté, la séance est liée à ta sortie" + (" et passée en « modifiée »." if ev["status"] ==
                                                                "modified" else "."), "link")
            return {"linked": True, "session_id": session_id, "day": s.day.isoformat(), "moved_from":
                    moved_from.isoformat() if moved_from else None, "feedback": self.store.feedback_for(
                        [session_id])[session_id] | {"auto": info}, "typing": self.typing()}

    # ------------------------------------------------------------ vues
    def _with_auto(self, sessions: list[dict]) -> None:
        """Marque dans les vues les retours liés à une sortie (automatiquement ou à la main)."""
        auto = self.store.get(AUTO_KEY) or {}
        names: dict[str, str] = {}
        if auto:                                          # v0.45 : nom actuel de la sortie (renommée sur Strava)
            days = [date.fromisoformat(v["start"][:10]) for v in auto.values() if v.get("start")]
            for a in self.store.activities(since=min(days) - timedelta(days=1)) if days else []:
                names[a.start_utc.isoformat()] = a.name or a.sport
        for s in sessions:
            info = auto.get(s["session_id"])
            if info and s.get("feedback"):
                cur = names.get(info.get("start"))
                s["feedback"] = {**s["feedback"], "auto": {**info, "activity": cur or info.get("activity")}}

    def _forget_auto(self, session_id: str) -> None:
        """v0.44 : le ressenti saisi par l'athlète GARDE le lien avec la sortie (il ne fait que le confirmer)."""
        links = self._links()
        if session_id in links:
            links[session_id] = {**links[session_id], "confirmed": True}
            self.store.set(AUTO_KEY, links)


__all__ = ["AUTO_KEY", "UNLINKED_KEY", "AutoValidateFlow", "evaluate", "match_sessions"]
