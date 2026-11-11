"""v0.38 — Phase spécifique : les 8 dernières semaines avant l'affûtage ressemblent à la course visée.

« Il faut ajouter de la spécificité à la fin de la prépa par rapport à la course. » Pour l'objectif principal
(trail), de J-71 à J-16 (l'affûtage commence à J-15, `pilot.planner`), les semaines de qualité du bloc
deviennent des semaines spécifiques, construites à partir du profil de la course (fichier de course et
simulateur s'il existe, sinon distance et D+) :

- côtes à la vitesse ascensionnelle de course (VVX : comme la montée du Puy de Dôme, km 34-37) ;
- durabilité en descente (quadriceps : 2750 m de D− sur la VVX) ;
- allure course sur terrain roulant (76 % de sentiers, portions roulantes : c'est là que se joue le chrono) ;
- sortie longue vallonnée (~32 m D+/km) à effort course, avec la nutrition du jour J ;
- une répétition générale 6 à 4 semaines avant, à l'heure du départ (6 h), avec le plan nutrition et le matériel ;
- un enchaînement le week-end seulement si la sortie longue est le week-end et que le week-end n'est pas limité.

Si l'athlète aime le double seuil, il est gardé (la 2e séance de qualité reste un double seuil, l'allure course
passe dans la sortie longue). Les volumes restent ceux du bloc : la sortie longue progresse d'au plus 10 % par
semaine (Frandsen 2025), les footings absorbent l'écart ; tout passe ensuite dans les mêmes garde-fous.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

TAPER_DAYS = 15                 # le modèle d'affûtage commence à J-15 (pilot.planner.TEMPLATE)
SPEC_WEEKS = 8
SPEC_LONG_MAX = 210             # sortie longue spécifique (min)
REHEARSAL_MAX = 240             # répétition générale (min)
REHEARSAL_WEEKS = (5, 6, 4)     # semaines avant la course, par ordre de préférence


def window(race_day: date) -> tuple[date, date]:
    """[début, fin) de la phase spécifique : 8 semaines qui se terminent la veille de l'affûtage."""
    end = race_day - timedelta(days=TAPER_DAYS)
    return end - timedelta(weeks=SPEC_WEEKS), end


def in_window(day: date, race_day: date) -> bool:
    a, b = window(race_day)
    return a <= day < b


@dataclass
class RaceShape:
    name: str
    short: str
    km: float
    dplus: float
    dpk: float                         # m D+ par km
    runnable: float | None             # part sentiers/pistes roulants (fichier de course)
    climb: dict | None                 # montée principale {name, km0, km1, gain_m, vam_m_h}
    descent_m: float
    start_clock: str
    race_pace: str | None              # allure sur le roulant (scénario cible), « 5:50 »
    race_h: float | None


def _pace(s_km: float | None) -> str | None:
    if not s_km:
        return None
    s = int(round(s_km))
    return f"{s // 60}:{s % 60:02d}"


def shape_for(name: str, day: date | None, km: float | None, dplus: float | None, kind: str = "trail") -> RaceShape | None:
    """Profil de la course : fichier de course + simulateur (profil approximatif) si connu, sinon km et D+."""
    if kind == "route":
        return None
    try:
        from . import race_sim as rs
        meta = rs.find_course(name, day)
    except Exception:                                              # noqa: BLE001
        meta = None
    if meta:
        try:
            course = rs.build_course(meta)
            anchor = rs.anchors(meta, None, None)
            sim = rs.simulate(course, anchor) if anchor else None
        except Exception:                                          # noqa: BLE001
            sim = None
        climb = None
        if sim and sim["climbs"]:
            c = max(sim["climbs"], key=lambda x: x["gain_m"] * max(x["grade_pct"], 1))
            climb = {k: c[k] for k in ("name", "km0", "km1", "gain_m", "vam_m_h")}
        runs = [t for s in (sim["sections"] if sim else []) for t in s["terrain"] if t["kind"] == "roulant"]
        rp = (sum(t["time_s"] for t in runs) / sum(t["km"] for t in runs)) if runs else None
        aliases = [a for a in meta.get("aliases", []) if a.isalpha() and len(a) <= 4]
        short = aliases[0].upper() if aliases else meta["race"].split("—")[0].strip()[:30]
        terr = meta.get("terrain") or {}
        return RaceShape(meta["race"], short, float(meta["official_km"]), float(meta["official_dplus"]),
                         float(meta["official_dplus"]) / float(meta["official_km"]),
                         (terr.get("paths", 0) + terr.get("tracks", 0)) or None, climb,
                         float(meta.get("official_dminus") or meta["official_dplus"]),
                         meta["start_local"][11:16], _pace(rp), sim["headline"]["finish_s"] / 3600 if sim else None)
    if not km:
        return None
    dp = float(dplus or 0)
    return RaceShape(name, name[:30], float(km), dp, dp / float(km), None, None, dp, "", None, None)


def _climb_txt(sh: RaceShape) -> str:
    c = sh.climb
    if c and c.get("gain_m", 0) >= 250:
        return f"comme la montée {c['name'].replace('Montée vers ', 'vers ')} (km {c['km0']:.0f}-{c['km1']:.0f}, +{c['gain_m']} m)"
    return f"comme les montées de {sh.short}"


def _vam_txt(sh: RaceShape) -> str:
    c = sh.climb
    return f" à ~{c['vam_m_h']} m/h" if c and c.get("vam_m_h") else ""


def specific_sessions(sh: RaceShape, k: int, weeks_out: int, light: bool, double: bool, rehearsal: bool,
                      pace: dict | None = None) -> dict:
    """Séances clés d'une semaine spécifique. `k` = rang dans la phase (0…), `weeks_out` = semaines avant la course.
    Retourne {"q": [[(intensité, description, minutes)], …], "long": (intensité, description), "rehearsal": bool}."""
    pace = pace or {}
    seuil = f" à {pace['seuil']}/km" if pace.get("seuil") else " au seuil (effort 7/10)"
    rp = f" ({sh.race_pace}/km sur le plat, effort 6/10)" if sh.race_pace else " (effort 6/10, ce que tu tiendrais toute la course)"
    from .session_content import Workout, build
    # v0.50 : texte ET durée tirés de la même structure (récupérations écrites en minutes)
    reps, on, rec = [(4, 8, 4), (5, 8, 4), (4, 12, 5), (3, 15, 5)][min(k, 3)] if not light else (3, 8, 4)
    climbs = ("hills", *build(Workout(
        20, reps, on * 60, rec * 60, cooldown=10, prefix="Côtes à l'allure de course : ",
        on_txt=f" de montée régulière (8-15 %){_vam_txt(sh)}", off_txt=" en redescendant en trottinant, pieds légers",
        middle=f" ; l'effort que tu tiendras {_climb_txt(sh)}, marche active si la pente dépasse ~15 %"),
        70 if light else 80 + 5 * min(k, 2)))
    dm = int(round(sh.descent_m / 50) * 50)
    descents = ("hills", *build(Workout(
        20, 6 if not light else 4, 180, 240, cooldown=10, prefix="Durabilité en descente : ",
        on_txt=" de descente rapide maîtrisée (cadence haute, regard loin)", off_txt=" en remontant en marche active",
        middle=f" ; prépare les quadriceps aux {dm} m de D− de {sh.short}"), 60 if light else 70))
    run_txt = (f"{sh.runnable:.0%} de sentiers et pistes sur {sh.short} : le roulant fait le chrono".replace("%", " %")
               if sh.runnable else "le roulant fait le chrono")
    flat = ("tempo", *build(Workout(
        20, 2 if light else 3, 12 * 60, 180, cooldown=10,
        prefix="Allure course sur terrain roulant : ", on_txt=f" à allure course{rp} sur chemin roulant",
        middle=f" ; {run_txt}"), 55 if light else 70))
    if double and not light:
        q2 = [("threshold", *build(Workout(15, 5, 360, 60, cooldown=10, pace=seuil,
                                           prefix="Double seuil, séance du matin : ",
                                           suffix=" On garde la vitesse pendant la phase spécifique."), 60)),
              ("threshold", *build(Workout(15, 10, 180, 60, cooldown=10, pace=seuil,
                                           prefix="Double seuil, séance du soir : ",
                                           suffix=" Même allure que le matin, pas plus vite."), 55))]
    else:
        q2 = [descents if k % 2 else flat]
    d_txt = f"~{sh.dpk:.0f} m D+/km comme {sh.short}" if sh.dpk >= 8 else "parcours roulant comme la course"
    if rehearsal:
        hh, _, mm = sh.start_clock.partition(":")
        start = f"départ à {int(hh)} h {mm} comme le jour J, " if sh.start_clock else ""
        long_ = ("long", f"Répétition générale : {start}parcours vallonné ({d_txt}), effort course{rp}. Plan nutrition de "
                 "course du simulateur (mêmes produits, mêmes prises), sac, chaussures et tenue du jour J : rien de "
                 "nouveau le jour de la course.")
    else:
        extra = (" dont 2 × 20 min à allure course sur le roulant" if double else "") if not light else ""
        long_ = ("long", f"Sortie longue vallonnée ({d_txt}), à effort course sur les deux derniers tiers{extra} ; "
                 "marche active dans les montées raides, et ravitaille-toi comme le jour J (plan nutrition du simulateur).")
    return {"q": [[climbs], q2], "long": long_, "rehearsal": rehearsal}


def rehearsal_week(race_day: date, mondays: list[date]) -> date | None:
    """Lundi de la semaine de la répétition générale parmi les semaines du bloc (5, 6 puis 4 semaines avant)."""
    for w in REHEARSAL_WEEKS:
        for m in mondays:
            if (race_day - m).days // 7 == w and in_window(m, race_day):
                return m
    return None


def phase_brief(profile: dict | None, today: date) -> str:
    """Une phrase pour le contexte du coach : la phase spécifique, en cours ou prochaine, et pourquoi."""
    if not profile or not profile.get("goal_date") or profile.get("goal_kind") == "route":
        return ""
    race_day = date.fromisoformat(str(profile["goal_date"])[:10])
    a, b = window(race_day)
    if not (a - timedelta(days=21) <= today < b):
        return ""
    sh = shape_for(profile.get("goal_name", ""), race_day, profile.get("goal_distance_km"), profile.get("goal_dplus_m"))
    if not sh:
        return ""
    what = (f"côtes à la vitesse ascensionnelle de course ({_climb_txt(sh)}), descentes pour les quadriceps, allure course "
            f"sur le roulant, sorties longues vallonnées (~{sh.dpk:.0f} m D+/km) avec la nutrition du jour J, et une "
            f"répétition générale 4 à 6 semaines avant" + (f" au départ de {sh.start_clock}" if sh.start_clock else ""))
    when = "en cours" if a <= today else "à venir"
    return (f"Phase spécifique {sh.short} {when} (du {a:%d/%m} au {(b - timedelta(days=1)):%d/%m}, avant l'affûtage) : {what}. "
            "Explique à l'athlète en quoi chaque séance clé ressemble à la course (le pourquoi).")


__all__ = ["window", "in_window", "RaceShape", "shape_for", "specific_sessions", "rehearsal_week", "phase_brief",
           "TAPER_DAYS", "SPEC_WEEKS"]
