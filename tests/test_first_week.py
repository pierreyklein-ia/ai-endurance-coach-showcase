"""v0.36 — le premier plan part de la dernière semaine réelle de l'athlète."""
from datetime import timedelta

from coach.diagnosis import Diagnosis, diagnose
from coach.onboarding import (
    Prefs,
    check_block,
    initial_block,
    is_bonus,
    last_week_anchor,
    proposal_message,
    week_meta,
)
from tests.test_onboarding import PROFILE, TODAY, act, trail_runner

START = TODAY + timedelta(days=1)                       # mercredi 28/10


def first_week(b, start=START):
    """Minutes de course prévues sur les 7 premiers jours (hors renfo, bonus, course)."""
    return sum(s.duration_min for s in b.sessions if start <= s.day < start + timedelta(days=7)
               and s.intensity not in ("rest", "race") and not is_bonus(s))


def hard_days(b, days=7, start=START):
    return [s.day for s in b.sessions if start <= s.day < start + timedelta(days=days)
            and s.intensity in ("vo2", "threshold")]


def hist_loads(acts):
    h = [0.0] * 200
    for a in acts:
        i = (a.local_day - (TODAY - timedelta(days=200))).days
        if 0 <= i < 200:
            h[i] += a.load
    return h


def road_runner(today=TODAY, minutes=55):
    """5 footings courts par semaine, jamais plus d'une heure : la sortie longue est bornée par Frandsen."""
    acts = []
    start = today - timedelta(days=120)
    for i in range(120):
        d = start + timedelta(days=i)
        if d.weekday() in (0, 1, 3, 4, 6):
            acts.append(act(d, minutes / 6, minutes + (5 if d.weekday() == 6 else 0), 10, "Footing"))
    return acts


def test_normal_week_continues_from_last_week():
    acts = trail_runner()
    d = diagnose(acts, TODAY)
    a = last_week_anchor(d, START)
    assert a.mode == "suite" and a.runs == 3 and a.minutes == 345 and a.long_min == 210
    b = initial_block(d, PROFILE, START)
    w1 = first_week(b)
    # on repart de la semaine réelle, sans saut : entre -10 % et +10 %
    assert 0.9 * a.minutes <= w1 <= 1.1 * a.minutes
    # l'ancien calcul (80 % de la semaine type x 0,85) repartait bien plus bas
    assert w1 > 0.8 * d.typical_week_min * 0.85 * 1.1
    longs = [s for s in b.sessions if s.intensity == "long" and s.day < START + timedelta(days=7)]
    assert longs and longs[0].duration_min == 150                  # proche de 3 h 30, plafond du bloc vitesse
    assert hard_days(b)                                            # l'intensité démarre normalement
    assert check_block(hist_loads(acts), b, START) == []           # garde-fou ACWR toujours respecté
    # progression ensuite : jamais plus de +30 % d'une semaine pleine à l'autre
    mins = [w["minutes"] for w in b.weeks]
    assert all(y <= 1.3 * x + 5 for x, y in zip(mins[1:], mins[2:]))


def test_explanatory_sentence_in_the_proposal():
    d = diagnose(trail_runner(), TODAY)
    b = initial_block(d, PROFILE, START)
    assert b.last_week.startswith("La semaine dernière : 3 sorties, 5 h 45")
    assert "sortie longue 3 h 30" in b.last_week and "m D+" in b.last_week and "on repart de là (+" in b.last_week
    txt = proposal_message(d, PROFILE, b, first=True, today=TODAY)
    assert "📅 La semaine dernière : 3 sorties" in txt
    assert week_meta(b)[b.weeks[0]["start"]]["last_week"] == b.last_week


def test_light_week_gives_a_progressive_return():
    acts = [a for a in trail_runner() if not (TODAY - timedelta(days=6) <= a.local_day)]   # 7 jours sans courir
    d = diagnose(acts, TODAY)
    a = last_week_anchor(d, START)
    assert a.mode == "reprise" and a.runs == 0
    b = initial_block(d, PROFILE, START)
    w1 = first_week(b)
    assert 0.6 * d.typical_week_min <= w1 <= 0.8 * d.typical_week_min
    assert hard_days(b) == []                                      # footing seulement la 1re semaine
    assert not [s for s in b.sessions if s.day < START + timedelta(days=7) and s.intensity == "long"]
    assert b.weeks[0]["kind"] == "Reprise" and b.intensity_from == START + timedelta(days=7)
    assert b.weeks[1]["minutes"] >= b.weeks[0]["minutes"]          # puis ça remonte
    assert b.last_week.startswith("Aucune sortie ces 7 derniers jours : la première semaine est une reprise "
                                  "progressive")
    one = [x for x in trail_runner() if not (TODAY - timedelta(days=6) <= x.local_day) or x.local_day.weekday() == 4]
    b1 = initial_block(diagnose(one, TODAY), PROFILE, START)
    assert b1.last_week.startswith("Semaine dernière très légère (1 sortie")


def test_big_week_or_race_starts_with_assimilation():
    acts = trail_runner()
    race_day = TODAY - timedelta(days=2)
    raced = acts + [act(race_day, 21.1, 95, 50, "Semi de Nantes")]
    d = diagnose(raced, TODAY)
    a = last_week_anchor(d, START)
    assert a.mode == "assimilation" and a.big_run and a.big_run.name == "Semi de Nantes"
    b = initial_block(d, PROFILE, START)
    assert first_week(b) < a.minutes                                # on assimile avant de construire
    assert all(x >= START + timedelta(days=3) and x >= race_day + timedelta(days=4) for x in hard_days(b))
    assert "Tu sors de Semi de Nantes" in b.last_week and "assimiler" in b.last_week
    # semaine très chargée sans course : idem
    extra = [act(TODAY - timedelta(days=k), 12, 80, 300, "Trail", "Trail") for k in (1, 3, 5)]
    d2 = diagnose(acts + extra, TODAY)
    a2 = last_week_anchor(d2, START)
    assert a2.mode == "assimilation" and a2.big_run is None and a2.minutes > 1.3 * d2.typical_week_min
    b2 = initial_block(d2, PROFILE, START)
    assert first_week(b2) <= 0.85 * d2.typical_week_min + 30
    assert hard_days(b2, 3) == []
    assert b2.last_week.startswith("Semaine dernière très chargée")


def test_long_run_respects_frandsen():
    acts = road_runner()
    d = diagnose(acts, TODAY)
    a = last_week_anchor(d, START)
    assert a.mode == "suite" and a.longest_30d_min == 60
    b = initial_block(d, PROFILE, START)
    wk = [s for s in b.sessions if START <= s.day < START + timedelta(days=7) and s.intensity not in ("rest", "race")]
    assert wk and max(s.duration_min for s in wk) <= 1.1 * 60       # l'ancienne sortie longue faisait 75 min
    assert any("110 %" in n for n in b.notes)
    # séances clés à durée fixe : ce sont les footings qui se raccourcissent pour rester à +10 % au plus
    assert first_week(b) <= 1.1 * a.minutes


def test_after_an_ultra_the_existing_recovery_is_unchanged():
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    assert last_week_anchor(d, START).mode == "recup"
    b = initial_block(d, PROFILE, START)
    assert [w["kind"] for w in b.weeks][:2] == ["Récupération", "Reprise"]
    assert "on commence par récupérer" in b.last_week


def test_old_diagnosis_without_detail_keeps_previous_behaviour():
    d = diagnose(trail_runner(), TODAY)
    old = Diagnosis.model_validate(d.model_dump(mode="json", exclude={"recent_runs"}))
    assert old.recent_runs is None and last_week_anchor(old, START) is None
    b = initial_block(old, PROFILE, START)
    assert b.last_week == "" and "📅" not in proposal_message(old, PROFILE, b, today=TODAY)
    # un bilan trop ancien (plus de 8 jours) n'ancre plus rien
    assert last_week_anchor(d, START + timedelta(days=10)) is None


def test_volume_factor_still_scales_the_anchored_week():
    d = diagnose(trail_runner(), TODAY)
    full = first_week(initial_block(d, PROFILE, START))
    less = first_week(initial_block(d, PROFILE, START, Prefs(volume_factor=0.8)))
    assert less < full
