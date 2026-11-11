"""v0.50 — Récap de la semaine, posté par le coach le soir du dernier jour de la semaine de l'athlète.

Contenu déterministe (0 token) :
- synchro : dernière synchro Strava, activités importées cette semaine, séances prévues sans sortie liée (« à valider
  ou à lier ? »), sorties douteuses ;
- séances clés (faite / modifiée / sautée / pas de retour), temps, km et D+ courus face au prévu et au volume maximum ;
- carrière : XP gagnée, trophées, niveau atteint, prochain événement ;
- semaine suivante : séances prévues, séances clés, alertes (deux jours durs d'affilée, volume maximum, course,
  affûtage) et ce qu'il faut confirmer (« ta sortie longue est lundi, ça te va ? »).
Une phrase d'encouragement du LLM peut s'ajouter (facultatif, budget permis) : voir coach/web/recap_flow.py.
Fonction pure, testée.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .onboarding import JOURS, JOURS_ABR, is_bonus

RECAP_HOUR = 20                    # le soir du dernier jour, à partir de 20 h (heure locale)
CATCH_UP_HOUR = 12                 # serveur arrêté le soir : rattrapage le lendemain matin, avant midi
KEY = {"threshold", "vo2", "long", "tempo", "hills"}
HARD = {"threshold", "vo2", "hills", "tempo", "long"}
STATUS = {"done": "faite ✓", "modified": "modifiée", "skipped": "sautée"}
TAPER_DAYS = 14


def _h(minutes: float) -> str:
    m = int(round(minutes / 5) * 5)
    return (f"{m // 60} h {m % 60:02d}".replace(" h 00", " h") if m >= 60 else f"{m} min")


def _d(d: date) -> str:
    return f"{JOURS_ABR[d.weekday()]} {d:%d/%m}"


def _km(x: float) -> str:
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}".replace(".", ",")


@dataclass
class RecapInput:
    week0: date                                    # premier jour de la semaine récapitulée
    today: date
    plan: list                                     # PlannedSession de la semaine
    next_plan: list                                # PlannedSession de la semaine suivante
    titles: dict = field(default_factory=dict)     # session_id -> titre affiché
    feedback: dict = field(default_factory=dict)   # session_id -> retour {status, rpe, auto…}
    acts: list = field(default_factory=list)       # Activity de la semaine
    last_sync: str | None = None                   # « 2026-11-08T19:42 »
    now: datetime | None = None
    wm: object | None = None                       # WeekMax (plafond et vitesses habituelles) ou None
    rates: object | None = None                    # WeekMax pour estimer km / D+ prévus (vitesses habituelles)
    xp_week: int = 0
    trophies: list = field(default_factory=list)   # libellés gagnés cette semaine
    levels: list = field(default_factory=list)     # niveaux atteints cette semaine
    event: dict | None = None                      # prochain événement de la carrière
    long_day: int | None = None                    # jour habituel de la sortie longue (profil)
    races: list = field(default_factory=list)      # [(nom, date)] objectifs à venir
    first_name: str = ""


def build(x: RecapInput) -> dict:
    """{"week_start", "title", "text", "sections": [{"id", "title", "lines"}], "data": {...}}."""
    from .diagnosis import is_run
    from .week_max import WeekMax, counted, totals
    end = x.week0 + timedelta(days=6)
    rates = x.rates or x.wm or WeekMax()
    sections = []
    title = lambda s: x.titles.get(s.session_id) or s.description[:40] or s.intensity   # noqa: E731

    # 1. synchro
    lines = []
    runs = [a for a in x.acts if is_run(a)]
    if x.last_sync:
        try:
            ls = datetime.fromisoformat(x.last_sync)
            old = x.now and (x.now - ls.replace(tzinfo=None)).total_seconds() > 36 * 3600
            lines.append(f"Dernière synchro Strava : {_d(ls.date())} à {ls:%H:%M}" + (" ⚠ ça date un peu : lance une "
                                                                                    "synchro (État)" if old else "."))
        except ValueError:
            lines.append(f"Dernière synchro Strava : {x.last_sync}.")
    else:
        lines.append("Pas de synchro Strava enregistrée : tes sorties arrivent-elles bien ?")
    lines.append(f"{len(x.acts)} activité(s) importée(s) cette semaine, dont {len(runs)} course(s).")
    due = [s for s in x.plan if s.day <= min(x.today, end) and s.intensity not in ("race",) and not s.is_race
           and not is_bonus(s) and s.session_id not in x.feedback]
    if due:
        lines.append(f"{len(due)} séance(s) sans sortie liée : à valider ou à lier ? ("
                     + ", ".join(f"{_d(s.day)} {title(s)}" for s in due[:5]) + ")")
    sus = [a for a in x.acts if getattr(a, "suspect", False)]
    if sus:
        lines.append(f"{len(sus)} sortie(s) à la durée douteuse (montre oubliée ?) : "
                     + ", ".join(f"{_d(a.local_day)} {a.name or a.sport}" for a in sus[:3])
                     + " ; elles ne comptent pas dans tes statistiques.")
    sections.append({"id": "sync", "title": "🔄 Synchro", "lines": lines})

    # 2. séances clés et volume
    lines = []
    keys = [s for s in x.plan if s.intensity in KEY and not s.is_race and not is_bonus(s)]
    status_count = {"done": 0, "modified": 0, "skipped": 0, "missing": 0}
    for s in keys:
        fb = x.feedback.get(s.session_id)
        st = fb.get("status") if fb else ("missing" if s.day < x.today or s.day == end else "upcoming")
        if st in status_count:
            status_count[st] += 1
        lbl = STATUS.get(st, "pas de retour" if st == "missing" else "à venir")
        lines.append(f"{title(s)} ({_d(s.day)}, {_h(s.duration_min)}) : {lbl}")
    if not keys:
        lines.append("Pas de séance clé prévue cette semaine.")
    done_min = sum(a.duration_s for a in runs) / 60
    done_km = sum(a.distance_m for a in runs) / 1000
    done_dp = sum(a.dplus_m or 0 for a in runs)
    planned = [s for s in x.plan if counted(s) and not is_bonus(s)]
    p_min, p_km, p_dp = totals(planned, rates)
    vs = f"prévu {_h(p_min)}" + (f", ~{_km(p_km)} km" if p_min else "") + (f", ~{p_dp:.0f} m D+" if p_dp >= 50 else "")
    cap = ""
    if x.wm is not None and getattr(x.wm, "any", lambda: False)():
        bits = [b for b in (_h(x.wm.minutes) if x.wm.minutes else "", f"{x.wm.km:g} km" if x.wm.km else "",
                            f"{x.wm.dplus} m D+" if x.wm.dplus else "") if b]
        over = ((x.wm.minutes and done_min > x.wm.minutes * 1.05) or (x.wm.km and done_km > x.wm.km * 1.05)
                or (x.wm.dplus and done_dp > x.wm.dplus * 1.05))
        cap = f" ; ton maximum : {', '.join(bits)}" + (" ⚠ dépassé" if over else "")
    lines.append(f"Total : {_h(done_min)} de course, {_km(done_km)} km, {done_dp:.0f} m D+ ({vs}{cap}).")
    sections.append({"id": "week", "title": "✅ Ta semaine", "lines": lines})

    # 3. carrière
    lines = []
    if x.xp_week:
        lines.append(f"+{x.xp_week} XP cette semaine.")
    if x.trophies:
        lines.append(("Trophées : " if len(x.trophies) > 1 else "Trophée : ") + ", ".join(x.trophies[:5]) + " 🏅")
    for lv in x.levels:
        lines.append(f"Niveau {lv['level']} atteint ({lv.get('rank', '')}) ! 🎉".replace(" ()", ""))
    if x.event:
        e = x.event
        when = "en cours" if e.get("live") else "demain" if e.get("days_left") == 1 else f"dans {e.get('days_left')} jours"
        lines.append(f"À venir : {e.get('label') or e.get('name')} ({when}, +{e.get('xp')} XP).")
    if not lines:
        lines.append("Pas d'XP cette semaine : la régularité se compte en semaines, la prochaine compte autant.")
    sections.append({"id": "career", "title": "🏆 Carrière", "lines": lines})

    # 4. semaine suivante
    n0, n6 = end + timedelta(days=1), end + timedelta(days=7)
    lines = []
    nxt = sorted([s for s in x.next_plan if n0 <= s.day <= n6], key=lambda s: (s.day, s.session_id))
    runs_next = [s for s in nxt if counted(s) and not is_bonus(s)]
    warnings, confirm = [], []
    if runs_next:
        nk = [s for s in runs_next if s.intensity in KEY]
        lines.append((f"{len(runs_next)} séance(s) de course, {_h(sum(s.duration_min for s in runs_next))} prévues"
                     + (" ; séances clés : " + ", ".join(f"{title(s)} {JOURS_ABR[s.day.weekday()]}" for s in nk)
                        if nk else "")).rstrip(".") + ".")
        hard_days = sorted({s.day for s in runs_next if s.intensity in HARD and s.planned_load > 0})
        pairs = [(a, b) for a, b in zip(hard_days, hard_days[1:]) if (b - a).days == 1]
        for a, b in pairs[:2]:
            warnings.append(f"deux jours durs d'affilée : {_d(a)} et {_d(b)}")
        if x.wm is not None and getattr(x.wm, "any", lambda: False)():
            from .week_max import over_text
            ov = over_text(totals(runs_next, x.wm), x.wm, 0.05)
            if ov:
                warnings.append("au-delà de ton volume maximum (" + " ; ".join(ov) + ")")
        longs = [s for s in runs_next if s.intensity == "long"]
        if longs and x.long_day is not None and longs[0].day.weekday() != x.long_day:
            confirm.append(f"ta sortie longue est {JOURS[longs[0].day.weekday()]} ({_h(longs[0].duration_min)}), "
                           "ça te va ?")
    else:
        lines.append("Rien n'est encore prévu : dis-moi tes disponibilités et je te prépare la suite.")
    for name, d in x.races:
        if n0 <= d <= n6:
            warnings.append(f"🏁 {name} {JOURS[d.weekday()]} {d:%d/%m} : on garde de la fraîcheur")
        elif n0 <= d - timedelta(days=TAPER_DAYS) <= n6 or (d - timedelta(days=TAPER_DAYS) < n0 <= d):
            warnings.append(f"affûtage pour {name} ({d:%d/%m}) : le volume baisse, l'intensité reste")
    lines += [f"⚠ {w}" for w in warnings] + [f"❓ {c}" for c in confirm]
    sections.append({"id": "next", "title": f"🔭 Semaine prochaine (du {_d(n0)} au {_d(n6)})", "lines": lines})

    hello = f"{x.first_name}, voici" if x.first_name else "Voici"
    head = f"📋 {hello} le récap de ta semaine du {_d(x.week0)} au {_d(end)}."
    text = head + "\n\n" + "\n\n".join(s["title"] + "\n" + "\n".join(f"• {ln}" for ln in s["lines"]) for s in sections)
    if due or confirm:
        text += "\n\nRéponds-moi ici : " + ("dis-moi pour les séances sans sortie liée" if due else "") + \
                (" ; " if due and confirm else "") + ("et confirme la semaine prochaine" if confirm else "") + "."
    return {"week_start": x.week0.isoformat(), "week_end": end.isoformat(), "title": "Récap de la semaine",
            "text": text, "sections": sections,
            "data": {"done_min": round(done_min), "done_km": round(done_km, 1), "done_dplus": round(done_dp),
                     "planned_min": round(p_min), "unlinked": [s.session_id for s in due],
                     "keys": status_count, "xp": x.xp_week, "warnings": warnings, "confirm": confirm,
                     "activities": len(x.acts), "suspect": len(sus)}}


def due_week(today: date, hour: int, week_start: int) -> tuple[date, bool] | None:
    """Semaine à récapituler maintenant, ou None : (début de semaine, rattrapage ?). Le soir du dernier jour à partir
    de 20 h ; rattrapage le lendemain matin (avant midi) si le serveur était arrêté."""
    w0 = today - timedelta(days=(today.weekday() - week_start) % 7)
    if today == w0 + timedelta(days=6) and hour >= RECAP_HOUR:
        return w0, False
    if today == w0 and hour < CATCH_UP_HOUR:
        return w0 - timedelta(days=7), True
    return None


def short_line(r: dict) -> str:
    """Résumé d'une ligne (notification push)."""
    d = r["data"]
    bits = [f"{_h(d['done_min'])} de course"]
    if d["keys"]["done"] or d["keys"]["modified"]:
        bits.append(f"{d['keys']['done'] + d['keys']['modified']} séance(s) clé(s) validée(s)")
    if d["unlinked"]:
        bits.append(f"{len(d['unlinked'])} à valider")
    if d["xp"]:
        bits.append(f"+{d['xp']} XP")
    return " · ".join(bits)


__all__ = ["RecapInput", "build", "due_week", "short_line", "RECAP_HOUR"]
