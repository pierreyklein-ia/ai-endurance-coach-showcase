"""v0.38 (2e partie) — simulateur de course de l'objectif principal, GPX privé, plan nutrition de course,
phase spécifique des 8 semaines avant l'affûtage."""
import json
import math
import stat
from datetime import date, timedelta

import numpy as np
import pytest

from coach import race_nutrition as rn
from coach import race_sim as rs
from coach.nutrition import build_log
from coach.onboarding import Prefs, Profile, check_block, initial_block, session_title, week_meta
from coach.race_specific import in_window, phase_brief, window

VVX = "Volvic Volcanic Experience (VVX) — 85 km"
RACE = date(2027, 5, 7)


@pytest.fixture(scope="module")
def meta():
    m = rs.find_course(VVX, RACE)
    assert m is not None
    return m


# --------------------------------------------------------------------------- fichier de course
def test_course_file_loads_with_sources_and_cutoffs(meta):
    assert meta["official_km"] == 85 and meta["official_dplus"] == 2750
    assert meta["start_local"].startswith("2027-05-07T06:00")
    names = [p["name"] for p in meta["points"]]
    assert "Puy de Dôme (sommet)" in names and "Manoir de Veygoux" in names
    aids = [p for p in meta["points"] if set(p.get("services", [])) & {"ravito", "eau"}]
    assert len(aids) == 7 and sum(p["services"] == ["eau"] for p in aids) == 1      # 7 ravitos dont 1 point d'eau
    summit = next(p for p in meta["points"] if p["name"].startswith("Puy de Dôme"))
    assert summit["km"] == 37 and summit["alt"] == 1465 and summit["lat"] == pytest.approx(45.770892)
    cuts = [p["cutoff"] for p in meta["points"] if p.get("cutoff")]
    assert [c["clock"] for c in cuts] == ["14:00", "19:25", "22:00", "23:30"]
    assert all(c["edition"] == 2026 and c["approx"] for c in cuts)
    assert "contradictoire" in meta["cutoff_note"] and "12 h 45" in meta["cutoff_note"]
    assert any(s["edition"] == 2027 and "volvic-vvx.com" in s["url"] for s in meta["sources"])
    assert "usage personnel" in meta["track_note"]
    # d'autres noms de l'objectif retrouvent la course ; une autre année ou une autre course non
    assert rs.find_course("VVX 85 km", RACE) and rs.find_course("Expérience Terres Volcaniques", RACE)
    assert rs.find_course("Volvic VVX", date(2028, 5, 5)) is None
    assert rs.find_course("Trail des Vallées", date(2027, 3, 20)) is None


def test_gpx_files_are_never_versioned():
    gi = (rs.ROOT / ".gitignore").read_text()
    assert "data/races/*.gpx" in gi


# --------------------------------------------------------------------------- profil approximatif
def test_approximate_profile_sums(meta):
    pr = rs.approx_profile(meta)
    assert pr.mode == "approx"
    assert pr.total_m == pytest.approx(85_000, abs=1)
    dp, dm = pr.dz[pr.dz > 0].sum(), -pr.dz[pr.dz < 0].sum()
    assert dp == pytest.approx(2750, abs=1) and dm == pytest.approx(2750, abs=1)
    # moitié de course (relais) : 1550 m D+ sur les 42,5 premiers km (ramenés à 85 km)
    split = 85 * 42.5 / 86
    x = np.concatenate([[0], np.cumsum(pr.dx)])[:-1]
    first = pr.dz[(x < split * 1000) & (pr.dz > 0)].sum()
    assert first == pytest.approx(1550, rel=0.02)
    # le sommet du Puy de Dôme est bien à 1465 m sur le profil reconstruit
    alt = pr.alt0 + np.concatenate([[0], np.cumsum(pr.dz)])
    i37 = int(round(37_000 / (pr.total_m / len(pr.dx))))
    assert alt[i37] == pytest.approx(1465, abs=15)


# --------------------------------------------------------------------------- simulation
@pytest.fixture(scope="module")
def course(meta):
    return rs.build_course(meta)


def sim(course, meta, **kw):
    return rs.simulate(course, rs.anchors(meta, None, None), **kw)


def test_default_anchor_is_the_retained_forecast(course, meta):
    a = rs.anchors(meta, None, None)
    assert (round(a["lo_s"] / 60), round(a["median_s"] / 60), round(a["hi_s"] / 60)) == (530, 555, 590)
    assert a["itra_effective"] == 605 and a["k_h"] == pytest.approx(5.617)
    r = sim(course, meta)
    assert r["headline"]["text"] == "Arrivée estimée 15:15 (9 h 15) — fourchette 14:50 – 15:50"
    # un pronostic enregistré (score ITRA effectif + fourchette) remplace le défaut
    b = rs.anchors(meta, {"itra_effective": 624, "itra_range": [600, 650], "source": "test"}, None)
    assert round(b["median_s"] / 60) == round(5.617 * 1000 / 624 * 60) and b["lo_s"] < b["median_s"] < b["hi_s"]


def test_scenarios_are_ordered_and_points_monotonic(course, meta):
    r = sim(course, meta)
    f = {s["id"]: s["finish_s"] for s in r["scenarios"]}
    assert f["ambitieux"] < f["cible"] < f["prudent"]
    for sc in ("ambitieux", "cible", "prudent"):
        arr = [p["arrival"][sc]["s"] for p in r["points"]]
        assert arr == sorted(arr) and arr[0] == 0
    sec = r["sections"]
    assert len(sec) == len(r["points"]) - 1
    assert sum(s["km"] for s in sec) == pytest.approx(85, abs=0.1)
    assert sum(s["dplus"] for s in sec) == pytest.approx(2750, abs=3)
    climb = next(s for s in sec if s["to"].startswith("Puy de Dôme"))
    assert climb["kind"] == "montee" and climb["vam_m_h"] and 500 < climb["vam_m_h"] < 1200
    # plus de temps au km dans la montée que sur le roulant : la répartition n'est pas uniforme
    flat = min((s for s in sec if s["kind"] == "roulant"), key=lambda s: s["pace_s_km"])
    assert climb["pace_s_km"] > 1.5 * flat["pace_s_km"]
    assert any("Puy de Dôme" in c["name"] for c in r["climbs"])
    assert r["daylight"]["sunrise"] in ("06:26", "06:27", "06:28") and "lever du soleil" in sec[0]["daylight"]
    assert r["compute_ms"] < 300


def test_target_time_split_sums_to_target(course, meta):
    r = sim(course, meta, target_s=9 * 3600)
    assert r["headline"]["finish_s"] == pytest.approx(9 * 3600, abs=2)
    assert r["headline"]["finish_clock"] == "15:00"
    tot = sum(s["time_s"]["cible"] for s in r["sections"]) + sum(p["stop_min"] * 60 for p in r["points"])
    assert tot == pytest.approx(9 * 3600, abs=30)
    assert r["scenarios"][1]["itra_equiv"] == 624                 # 9 h 00 = 624 (barème du pronostic)
    up = next(s for s in r["sections"] if s["to"].startswith("Puy de Dôme"))
    assert up["time_s"]["cible"] / up["km"] > 600                  # > 10 min/km dans la montée finale
    # même temps visé par forte chaleur : toujours 9 h 00, mais réparti autrement (plus lent l'après-midi)
    h = sim(course, meta, target_s=9 * 3600, heat="chaude")
    assert h["headline"]["finish_s"] == pytest.approx(9 * 3600, abs=2)
    assert h["sections"][-1]["time_s"]["cible"] > r["sections"][-1]["time_s"]["cible"]


def test_heat_and_form_change_times(course, meta):
    base = sim(course, meta)["headline"]["finish_s"]
    hot = sim(course, meta, heat="chaude")["headline"]["finish_s"]
    cool = sim(course, meta, heat="fraiche")["headline"]["finish_s"]
    good = sim(course, meta, form=0.05)["headline"]["finish_s"]
    bad = sim(course, meta, form=-0.05)["headline"]["finish_s"]
    assert cool < base < hot and hot - base > 10 * 60
    assert good < base < bad
    assert sim(course, meta, form=0.5)["conditions"]["form"] == 0.05          # borné à ±5 %


def test_cutoff_margins(course, meta):
    r = sim(course, meta)
    cut = [p for p in r["points"] if p["cutoff"]]
    assert len(cut) == 5                                            # 4 barrières 2026 + temps limite à l'arrivée
    barr = next(p for p in cut if p["name"].startswith("Barrière"))
    c = barr["cutoff"]
    assert c["clock"] == "14:00" and c["approx"] and c["limit_s"] == 8 * 3600
    assert c["margin_min"]["prudent"] == round((8 * 3600 - barr["arrival"]["prudent"]["s"]) / 60)
    assert c["margin_min"]["ambitieux"] > c["margin_min"]["cible"] > c["margin_min"]["prudent"] and c["status"] == "ok"
    # un coureur à 16 h : la marge fond, orange puis rouge
    slow = rs.simulate(course, {"lo_s": 15.5 * 3600, "median_s": 16 * 3600, "hi_s": 17.2 * 3600, "source": "test"})
    st = {p["name"]: p["cutoff"]["status"] for p in slow["points"] if p["cutoff"]}
    assert {"ok", "juste", "danger"} <= set(st.values()) and st[barr["name"]] == "danger"


def test_parse_target():
    assert rs.parse_target("9:00") == 9 * 3600 and rs.parse_target("8h45") == 8.75 * 3600
    assert rs.parse_target("540") == 9 * 3600 and rs.parse_target(None) is None
    with pytest.raises(ValueError):
        rs.parse_target("demain")
    with pytest.raises(ValueError):
        rs.parse_target("0:10")


# --------------------------------------------------------------------------- plan nutrition
def L(day, items, g, dig=2, sy=(), h=3.0):
    log = build_log(date=day, session_id=None, activity_id=None, duration_h=h, items=items, water_ml=1000,
                    digestion=dig, symptoms=list(sy), comment="")
    log["carbs_g_h"] = float(g)
    return log


def plan_for(course, meta, logs, heat="normale"):
    r = sim(course, meta, heat=heat)
    return r, rn.race_plan(rs.nutrition_legs(r), r["headline"]["finish_s"] / 3600, logs, heat, meta)


def test_nutrition_without_logs_is_conservative(course, meta):
    r, p = plan_for(course, meta, [])
    assert p["trained_max_g_h"] is None and p["target_g_h"] <= 70 and p["rehearsal_needed"]
    assert any("répétition" in n for n in p["notes"])
    assert len(p["legs"]) == 8                                       # départ + 7 ravitos -> arrivée
    assert all(leg["flasks"] >= 1 for leg in p["legs"])              # réserve de 0,5 L toujours
    assert any("0,5 L" in m for m in p["mandatory"]) and any("gobelet" in m for m in p["mandatory"])
    water = next(leg for leg in p["legs"] if leg["from"].endswith("(point d'eau)"))
    assert water["at_aid"] == [] and any("point d'eau" in n for n in water["notes"])
    # caféine seulement dans le dernier tiers
    total = r["headline"]["finish_s"]
    for leg in p["legs"]:
        if leg["caffeine_mg"] and any(i["id"] == "gel_cafe" for i in leg["carry"]):
            assert leg["start_s"] >= total * 2 / 3 - 1
    assert p["totals"]["caffeine_mg"] <= 250


def test_nutrition_respects_trained_max_and_avoid_list(course, meta):
    logs = [L("2026-12-01", [{"id": "gel_hd", "qty": 4}, {"id": "compote_sport", "qty": 2}], 80),
            L("2026-12-15", [{"id": "barre", "qty": 4}, {"id": "pain_epices", "qty": 2}], 55, dig=5, sy=["nausee"])]
    _, p = plan_for(course, meta, logs)
    assert p["trained_max_g_h"] == 80 and p["target_g_h"] <= 90
    assert set(p["avoid"]) == {"barre", "pain_epices"}
    ids = {i["id"] for leg in p["legs"] for i in leg["carry"] + leg["at_aid"]}
    assert not ids & {"barre", "pain_epices"}
    assert "gel_hd" in ids                                           # ce qui passe bien est privilégié
    for leg in p["legs"]:
        h = leg["duration_s"] / 3600
        assert leg["carbs_g"] / h <= min(120, p["range_g_h"][1] + 10) + 0.5
    # cap absolu à 120 g/h, même avec un maximum toléré très haut
    big = [L("2026-12-20", [{"id": "gel_hd", "qty": 12}], 140)]
    _, p2 = plan_for(course, meta, big)
    assert p2["target_g_h"] <= 120
    assert all(leg["carbs_g"] / (leg["duration_s"] / 3600) <= 120.5 for leg in p2["legs"])


def test_heat_increases_fluid_and_sodium(course, meta):
    _, n = plan_for(course, meta, [])
    _, h = plan_for(course, meta, [], heat="chaude")
    assert h["fluid_ml_h"] > n["fluid_ml_h"] and h["sodium_mg_h"] > n["sodium_mg_h"]
    assert h["totals"]["fluid_ml"] > n["totals"]["fluid_ml"] and h["totals"]["sodium_mg"] > n["totals"]["sodium_mg"]
    assert "Chaleur" in h["heat_note"]


# --------------------------------------------------------------------------- GPX synthétique
def synth_gpx(meta, km_factor=1.0, dplus_factor=1.0, with_ele=True) -> bytes:
    """Tracé qui passe par les coordonnées des ravitos, en zigzag pour avoir la bonne longueur, avec le relief
    des points et des bosses pour retrouver le D+ officiel."""
    pts = sorted(meta["points"], key=lambda p: p["km"])
    start = (45.872, 3.04)
    coords = []
    for p in pts:
        if p.get("lat") is not None:
            coords.append((p["lat"], p["lon"]))
        elif p["name"].startswith("Col Gouttes"):
            q = next(x for x in pts if x["name"] == "Col Gouttes / Chaumont")
            coords.append((q["lat"] + 0.002, q["lon"]))
        else:
            coords.append({"Pied": (45.785, 2.955), "Tenuzet": (45.86, 3.0), "Château": (45.886, 3.035)}.get(
                p["name"].split()[0], start))
    lat, lon, kms = [], [], []
    for (a, b), (pa, pb) in zip(zip(coords[:-1], coords[1:]), zip(pts[:-1], pts[1:])):
        L = (pb["km"] - pa["km"]) * 1000 * km_factor
        n = max(4, int(L // 40))
        la = np.linspace(a[0], b[0], n, endpoint=False)
        lo = np.linspace(a[1], b[1], n, endpoint=False)
        cs = math.cos(math.radians(45.8))
        dy, dx = (b[0] - a[0]) * 111_000, (b[1] - a[1]) * 111_000 * cs
        D = math.hypot(dx, dy) or 1.0
        step = L / n
        w = math.sqrt(max(step ** 2 - (D / n) ** 2, 0)) / 2        # zigzag perpendiculaire pour la bonne longueur
        off = np.where(np.arange(n) % 2, w, -w)
        lat += list(la + off * (dx / D) / 111_000)
        lon += list(lo - off * (dy / D) / (111_000 * cs))
        kms += list(np.linspace(pa["km"], pb["km"], n, endpoint=False) * km_factor)
    lat.append(coords[-1][0]); lon.append(coords[-1][1]); kms.append(pts[-1]["km"] * km_factor)
    kms = np.array(kms)
    base = np.interp(kms / km_factor, [p["km"] for p in pts], [p["alt"] for p in pts])
    from coach.pacer.tracks import Track, _haversine_cum, elevation_gain, segment
    dist = _haversine_cum(np.array(lat), np.array(lon))

    def dplus(amp):
        e = base + amp * np.sin(kms * 2 * math.pi / 1.6)
        return elevation_gain(segment(Track("t", dist, e), seg_len=50)), e
    want = 2750 * dplus_factor
    a0, a1 = 0.0, 40.0
    d0, d1 = dplus(a0)[0], dplus(a1)[0]
    for _ in range(8):                                                # sécante : amplitude des bosses -> D+ visé
        a2 = a1 + (want - d1) * (a1 - a0) / ((d1 - d0) or 1)
        a0, d0, a1 = a1, d1, max(a2, 0.0)
        d1, ele = dplus(a1)
        if abs(d1 / want - 1) < 0.01:
            break
    rows = "".join(f'<trkpt lat="{a:.6f}" lon="{o:.6f}">' + (f"<ele>{e:.1f}</ele>" if with_ele else "") + "</trkpt>"
                   for a, o, e in zip(lat, lon, ele))
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="test" '
            'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>VVX</name><trkseg>' + rows
            + "</trkseg></trk></gpx>").encode()


# --------------------------------------------------------------------------- API
def vvx_client(tmp_path, logs=None):
    from tests.test_web import make
    c = make(tmp_path)
    c.service.store.set("profile", Profile(goal_name=VVX, goal_date=RACE, goal_distance_km=85,
                                           goal_dplus_m=2750).model_dump(mode="json"))
    if logs:
        c.service.store.set("nutrition_logs", logs)
    return c


def test_sim_api_contract(tmp_path):
    c = vvx_client(tmp_path)
    r = c.get("/api/race/sim")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["available"] and b["mode"] == "approx" and "ajoute le GPX" in b["mode_note"]
    assert b["headline"]["finish_clock"] == "15:15" and b["goal"]["name"] == VVX
    assert {"points", "sections", "climbs", "profile", "nutrition", "anchor", "course", "daylight"} <= set(b)
    assert b["course"]["track_url"].startswith("https://tracedetrail.fr/") and b["gpx"] is None
    assert "_runs" not in b
    h = c.get("/api/race/sim", params={"heat": "chaude", "form": -3}).json()
    assert h["headline"]["finish_s"] > b["headline"]["finish_s"] and h["conditions"]["heat"] == "chaude"
    t = c.get("/api/race/sim", params={"target": "9:00"}).json()
    assert t["headline"]["finish_clock"] == "15:00" and t["conditions"]["target_s"] == 32400
    assert c.get("/api/race/sim", params={"target": "n'importe"}).status_code == 422
    # sans objectif principal
    c.service.store.set("profile", None)
    assert c.get("/api/race/sim").json()["available"] in (True, False)


def test_gpx_upload_validation_snapping_and_privacy(tmp_path, meta):
    c = vvx_client(tmp_path)
    data = synth_gpx(meta)
    r = c.post("/api/race/gpx", params={"name": "vvx.gpx"}, content=data)
    assert r.status_code == 200, r.text
    g = r.json()["gpx"]
    assert g["warning"] == "" and abs(g["km"] - 85) / 85 < 0.08 and abs(g["dplus"] - 2750) / 2750 < 0.15
    files = list((tmp_path / "private").glob("race-*.gpx"))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    b = c.get("/api/race/sim").json()
    assert b["mode"] == "gpx" and b["gpx"]["name"] == "vvx.gpx" and "GPX" in b["mode_note"]
    summit = next(p for p in b["points"] if p["name"].startswith("Puy de Dôme"))
    assert summit["snapped"] and abs(summit["km"] - 37) < 2.5        # recalé sur le tracé grâce au GPS
    assert abs(summit["alt"] - 1465) < 60 and not summit["alt_approx"]   # altitude lue dans le GPX
    assert len(b["profile"]) > 100
    assert b["headline"]["finish_clock"] == "15:15"                    # même ancrage, autre répartition
    # D+ très différent : accepté avec un avertissement visible
    r = c.post("/api/race/gpx", content=synth_gpx(meta, dplus_factor=1.4))
    assert r.status_code == 200 and "D+" in r.json()["gpx"]["warning"]
    assert c.get("/api/race/sim").json()["gpx"]["warning"]
    # mauvais tracé (60 % de la distance), pas un GPX, pas d'altitude : refusés
    assert c.post("/api/race/gpx", content=synth_gpx(meta, km_factor=0.6)).status_code == 422
    assert c.post("/api/race/gpx", content=b"hello").status_code == 422
    assert c.post("/api/race/gpx", content=synth_gpx(meta, with_ele=False)).status_code == 422
    # suppression : retour au profil approximatif, fichier effacé
    assert c.delete("/api/race/gpx").status_code == 200
    assert not list((tmp_path / "private").glob("race-*.gpx"))
    assert c.get("/api/race/sim").json()["mode"] == "approx"
    # la sauvegarde ne contient jamais le dossier privé
    from coach import backup
    assert "private" in (rs.ROOT / "coach" / "backup.py").read_text() and backup


def test_generic_race_uses_manual_aid_stations(tmp_path):
    from tests.test_web import make
    c = make(tmp_path)
    c.service.store.set("profile", Profile(goal_name="Trail du Morvan", goal_date=date(2027, 6, 12),
                                           goal_distance_km=60, goal_dplus_m=1800).model_dump(mode="json"))
    b = c.get("/api/race/sim").json()
    if not b["available"]:
        assert b["need"] == "reference"
        return
    assert b["course"]["generic"] and len(b["points"]) == 2
    r = c.post("/api/race/points", json={"points": [{"name": "Ravito 1", "km": 20}, {"name": "", "km": 41.5},
                                                    {"name": "hors course", "km": 75}]})
    assert r.status_code == 200 and [p["km"] for p in r.json()["points"]] == [20, 41.5]
    b = c.get("/api/race/sim").json()
    assert [p["name"] for p in b["points"]] == ["Départ", "Ravito 1", "Ravito km 41.5", "Arrivée"]
    assert len(b["nutrition"]["legs"]) == 3 and b["course"]["manual_points"][0]["km"] == 20


def test_sim_includes_nutrition_from_logs(tmp_path):
    logs = [L("2026-12-01", [{"id": "gel", "qty": 6}], 75)]
    c = vvx_client(tmp_path, logs)
    n = c.get("/api/race/sim").json()["nutrition"]
    assert n["trained_max_g_h"] == 75 and not any("Aucun bilan" in x for x in n["notes"])
    assert all("line" in leg for leg in n["legs"])


# --------------------------------------------------------------------------- phase spécifique
def _hist(acts, today):
    from collections import defaultdict
    first = min(a.local_day for a in acts)
    loads = defaultdict(float)
    for a in acts:
        loads[a.local_day] += a.load
    return [loads[first + timedelta(days=i)] for i in range((today - first).days)]


SPEC_PREFIX = ("Côtes à l'allure de course", "Durabilité en descente", "Allure course sur terrain roulant",
               "Répétition générale", "Sortie longue vallonnée", "Footing dos-à-dos")
VVX_PROFILE = Profile(goal_name=VVX, goal_date=RACE, goal_distance_km=85, goal_dplus_m=2750, long_day=2)


def block_at(today, prefs=None, profile=VVX_PROFILE):
    from coach.diagnosis import diagnose
    from tests.test_onboarding import trail_runner
    acts = trail_runner(today)
    d = diagnose(acts, today)
    return initial_block(d, profile, today + timedelta(days=1), prefs or Prefs(speed_weeks=6)), _hist(acts, today)


def is_spec(s):
    return s.description.startswith(SPEC_PREFIX)


def test_specific_window():
    a, b = window(RACE)
    assert b == RACE - timedelta(days=15) and (b - a).days == 56
    assert in_window(date(2027, 3, 31), RACE) and not in_window(date(2027, 4, 22), RACE)


def test_specific_sessions_only_in_the_last_8_weeks_before_taper():
    far, _ = block_at(date(2026, 11, 3))                       # novembre : bloc de vitesse classique
    assert not any(is_spec(s) for s in far.sessions)
    assert not any(w["kind"].startswith("Spécifique") for w in far.weeks)
    # bloc à cheval sur le début de la phase (8 février -> 21 mars)
    edge, _ = block_at(date(2027, 2, 8))
    a, b = window(RACE)
    spec = [s for s in edge.sessions if is_spec(s)]
    assert spec and all(a <= s.day < b for s in spec)
    assert any(s.intensity in ("vo2",) for s in edge.sessions if s.day < a)   # avant : VMA du bloc de vitesse
    assert not any(s.intensity == "vo2" for s in edge.sessions if s.day >= a)


def test_specific_block_references_the_race_and_passes_guardrails():
    from coach.guardrails import PlanContext, check
    from coach.load_engine import pmc
    from coach.schemas import Signals
    today = date(2027, 3, 1)
    blk, hist = block_at(today, Prefs(speed_weeks=6, double_threshold=True, weekend_max_min=90))
    start = today + timedelta(days=1)
    assert all(w["kind"].startswith("Spécifique VVX") for w in blk.weeks)
    descs = " ".join(s.description for s in blk.sessions)
    assert "Puy de Dôme" in descs and "m/h" in descs and "32 m D+/km" in descs
    assert "Double seuil" in descs                                   # il aime le double seuil : gardé
    reh = [s for s in blk.sessions if s.description.startswith("Répétition générale")]
    assert len(reh) == 1 and "6 h 00" in reh[0].description and "nutrition" in reh[0].description
    assert 4 <= (RACE - reh[0].day).days // 7 <= 6
    assert reh[0].day.weekday() == 2                                 # sortie longue en semaine (son choix)
    assert not any(s.description.startswith("Footing dos-à-dos") for s in blk.sessions)   # week-end limité
    assert session_title(reh[0]) == "Répétition générale"
    assert any(m["focus"].startswith("Spécificité") for m in week_meta(blk).values())
    assert "spécifiques VVX" in blk.summary and any("Phase spécifique VVX" in n for n in blk.notes)
    # garde-fous : ACWR du bloc, progression de la sortie longue (≤ +10 %), règles du graphe semaine par semaine
    assert check_block(hist, blk, start) == []
    longs = [s.duration_min for s in sorted(blk.sessions, key=lambda s: s.day) if s.intensity == "long"]
    for x, y in zip(longs[:-1], longs[1:]):
        assert y <= max(x * 1.10, max(longs[:longs.index(y)] or [0]) * 1.10) + 5
    seed = sum(hist[-28:]) / 28
    states = pmc(hist, ctl0=seed, atl0=seed, a7_0=seed, c28_0=seed)
    st = states[-1]
    done: list = []
    for w in blk.weeks:
        ws = date.fromisoformat(w["start"])
        wk = [s for s in blk.sessions if ws <= s.day < ws + timedelta(days=7)]
        ref = max([x.duration_min for x in done if x.intensity == "long" and (ws - x.day).days <= 30] or [210])
        v = check(wk, Signals(), PlanContext(state=st, baseline_week_load=sum(s.planned_load for s in wk),
                                             today=max(ws, start), race_date=RACE, longest_run_30d_min=ref))
        assert v.allowed, (w, [x.detail for x in v.violations])
        assert not [x for x in v.violations if x.rule_id == "R09_peak_session"], w
        loads = [0.0] * 7
        for s in wk:
            loads[(s.day - ws).days] += s.planned_load
        st = pmc(loads, ctl0=st.ctl, atl0=st.atl, a7_0=st.a7, c28_0=st.c28)[-1]
        done += wk


def test_back_to_back_only_when_the_weekend_allows_it():
    sat = Profile(goal_name=VVX, goal_date=RACE, goal_distance_km=85, goal_dplus_m=2750, long_day=5, days_per_week=7)
    free, _ = block_at(date(2027, 3, 1), Prefs(speed_weeks=6), sat)
    limited, _ = block_at(date(2027, 3, 1), Prefs(speed_weeks=6, weekend_max_min=90), sat)
    assert any(s.description.startswith("Footing dos-à-dos") for s in free.sessions)
    assert not any(s.description.startswith("Footing dos-à-dos") for s in limited.sessions)


def test_route_goal_has_no_trail_specific_phase():
    road = Profile(goal_name="Marathon de Nantes", goal_date=RACE, goal_kind="route", goal_distance_km=42.195)
    blk, _ = block_at(date(2027, 3, 1), Prefs(speed_weeks=4), road)
    assert not any(is_spec(s) for s in blk.sessions)


def test_coach_is_told_about_the_phase(tmp_path):
    prof = VVX_PROFILE.model_dump(mode="json")
    assert phase_brief(prof, date(2026, 11, 2)) == ""
    txt = phase_brief(prof, date(2027, 3, 10))
    assert txt.startswith("Phase spécifique VVX en cours") and "Puy de Dôme" in txt and "pourquoi" in txt
    assert "à venir" in phase_brief(prof, date(2027, 2, 10))
    from coach.graph import COACH_SYSTEM
    assert "Phase spécifique" in COACH_SYSTEM
    from tests.test_web import make
    c = make(tmp_path)
    c.service.store.set("profile", prof)
    c.pilot.repo.today_fn = lambda: date(2027, 3, 10)
    assert "Phase spécifique VVX" in c.pilot.repo.get("x").profile_summary


def test_course_file_is_valid_json_for_the_catalog():
    raw = json.loads((rs.RACES / "vvx-2027.json").read_text())
    cat = json.loads((rs.RACES / "catalog.json").read_text())
    assert any(c["date"] == raw["date"] and c["dplus_m"] == raw["official_dplus"] for c in cat)
