"""Import des activités : archive de compte Strava (export RGPD) et fichiers FIT/GPX.

Charge d'une sortie (même unité que `planned_load`, 1 h au seuil = 100) :
1. flux FC du FIT  -> hrTSS (load_engine, nettoyage GIGO inclus)     source "hrTSS"
2. FC moyenne CSV  -> hrTSS approché (FC constante = FC moyenne)     source "csv_avg_hr"
3. rien            -> forfait horaire par sport (à défaut)            source "estimate"
L'approximation 2 sous-estime un peu les séances très variables (fractionné).
"""
from __future__ import annotations

import csv
import gzip
import io
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from ..load_engine import hr_tss, session_load
from ..pacer.tracks import elevation_gain, load_fit, load_gpx, segment
from .config import PilotSettings
from .store import Activity, PilotStore, effective_duration

SPORTS = {  # FIT (fitdecode), CSV et API Strava (sport_type) -> libellé. Clés normalisées (minuscules, sans espace)
    "running": "Course", "run": "Course", "virtualrun": "Course", "treadmill": "Course",
    "trailrun": "Trail", "trailrunning": "Trail",
    "hiking": "Rando", "hike": "Rando", "mountaineering": "Rando", "snowshoe": "Rando",
    "walking": "Marche", "walk": "Marche",
    "cycling": "Vélo", "ride": "Vélo", "virtualride": "Vélo", "ebiking": "Vélo", "ebikeride": "Vélo",
    "mountainbikeride": "Vélo", "emountainbikeride": "Vélo", "gravelride": "Vélo", "velomobile": "Vélo",
    "handcycle": "Vélo",
    # v0.25 : l'API Strava écrit « WeightTraining », « Crossfit », « HighIntensityIntervalTraining »…
    "training": "Renfo", "weighttraining": "Renfo", "workout": "Renfo", "fitnessequipment": "Renfo",
    "crossfit": "Renfo", "highintensityintervaltraining": "Renfo", "strengthtraining": "Renfo",
    "pilates": "Renfo", "yoga": "Renfo",
    "swimming": "Natation", "swim": "Natation",
    "ski": "Ski", "crosscountryskiing": "Ski", "backcountryski": "Ski", "backcountryskiing": "Ski",
    "nordicski": "Ski", "alpineski": "Ski", "rollerski": "Ski",
}
# Charge horaire par défaut quand aucune FC n'est disponible (≈ zone 2).
HOURLY_DEFAULT = {"Course": 55, "Trail": 60, "Rando": 35, "Marche": 20, "Vélo": 45,
                  "Renfo": 30, "Natation": 45, "Ski": 50}


LABELS = set(SPORTS.values())


def sport_label(raw: str | None) -> str:
    if (raw or "").strip() in LABELS:
        return raw.strip()
    r = re.sub(r"[\s_\-]", "", (raw or "").strip().lower())
    return SPORTS.get(r, (raw or "Autre").strip().capitalize() or "Autre")


def _local_day(start_utc: datetime, tz: str) -> date:
    return start_utc.astimezone(ZoneInfo(tz)).date()


def load_from_avg_hr(avg_hr: float | None, duration_s: float, sport: str, st: PilotSettings) -> tuple[float, str]:
    if avg_hr and avg_hr > st.hr_rest + 10 and duration_s > 0:
        n = max(int(duration_s // 10), 1)                       # pas de 10 s suffit (FC constante)
        return round(hr_tss(np.full(n, float(avg_hr)), st.hr_rest, st.hr_max, st.threshold_hr,
                            st.sex, dt_s=duration_s / n), 1), "csv_avg_hr"
    return round(HOURLY_DEFAULT.get(sport, 35) * duration_s / 3600, 1), "estimate"


# --------------------------------------------------------------------------- #
# Fichiers FIT / GPX
# --------------------------------------------------------------------------- #
def _fit_summary(path: Path) -> dict:
    import fitdecode
    out: dict = {}
    with fitdecode.FitReader(str(path)) as fit:
        for fr in fit:
            if isinstance(fr, fitdecode.FitDataMessage) and fr.name == "session":
                def g(n):
                    return fr.get_value(n, fallback=None)
                out = {"sport": g("sport"), "sub_sport": g("sub_sport"), "start": g("start_time"),
                       "elapsed": g("total_elapsed_time"), "timer": g("total_timer_time"),
                       "distance": g("total_distance"), "ascent": g("total_ascent"),
                       "avg_hr": g("avg_heart_rate")}
                break
    return out


def activity_from_file(path: Path, st: PilotSettings, origin: str = "fichier") -> Activity:
    name = path.name
    if name.endswith(".gz"):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / name[:-3]
            raw.write_bytes(gzip.decompress(path.read_bytes()))
            return activity_from_file(raw, st, origin)
    ext = path.suffix.lower()
    if ext == ".fit":
        tr = load_fit(path)
        summ = _fit_summary(path)
        sport = sport_label("trail_running" if summ.get("sub_sport") == "trail" else summ.get("sport"))
    elif ext == ".gpx":
        tr, summ, sport = load_gpx(path), {}, "Course"
        if tr.t is None:
            raise ValueError("GPX sans horodatage : c'est un tracé, pas une sortie")
    else:
        raise ValueError(f"format non géré : {ext} (FIT ou GPX attendu)")

    start = summ.get("start") or tr.start_utc or datetime.now(UTC)
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    timer = float(summ["timer"]) if summ.get("timer") else None
    raw_elapsed = float(summ.get("elapsed") or tr.t[-1])
    duration = effective_duration(raw_elapsed, timer)   # v0.48 : montre oubliée
    dplus = float(summ.get("ascent") or elevation_gain(segment(tr)))
    dist = float(summ.get("distance") or tr.dist[-1])
    avg_hr, load, source = None, None, None
    if tr.hr is not None and np.isfinite(tr.hr).sum() > 30:
        t1 = np.arange(0, tr.t[-1], 1.0)                         # FC rééchantillonnée à 1 Hz
        ok = np.isfinite(tr.hr)
        hr1 = np.interp(t1, tr.t[ok], tr.hr[ok])
        avg_hr = float(summ.get("avg_hr") or np.mean(hr1))
        val, src = session_load(hr1, hr_rest=st.hr_rest, hr_max=st.hr_max, lthr=st.threshold_hr,
                                duration_min=duration / 60, rpe=None, srpe_to_tss=1.0, sex=st.sex)
        if np.isfinite(val):
            load, source = round(val, 1), src
            if duration < raw_elapsed - 1:                       # flux de 24 h : charge au prorata du mouvement
                load = round(load * duration / raw_elapsed, 1)
    if load is None:
        load, source = load_from_avg_hr(avg_hr, duration, sport, st)
    act = Activity(start, _local_day(start, st.tz), sport, duration, dist, dplus, avg_hr,
                   load, source, origin, name, timer)
    if tr.t is not None and len(tr.t) > 30:                          # v0.43 : type de séance couru
        from ..session_type import profile_from_streams
        act.profile = profile_from_streams(tr.t, tr.hr, tr.dist, tr.alt, lthr=st.threshold_hr)
    return act


def activity_from_bytes(data: bytes, filename: str, st: PilotSettings, origin: str = "telegram") -> Activity:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "sortie.fit"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / safe
        p.write_bytes(data)
        return activity_from_file(p, st, origin)


# --------------------------------------------------------------------------- #
# Archive Strava (Paramètres -> Mon compte -> Télécharger ou supprimer votre compte)
# --------------------------------------------------------------------------- #
_MONTHS = {"jan": 1, "janv": 1, "feb": 2, "fev": 2, "févr": 2, "fevr": 2, "mar": 3, "mars": 3,
           "apr": 4, "avr": 4, "may": 5, "mai": 5, "jun": 6, "juin": 6, "jul": 7, "juil": 7,
           "aug": 8, "aou": 8, "août": 8, "aout": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
           "dec": 12, "déc": 12}


def parse_strava_date(s: str) -> datetime | None:
    """« Sep 29, 2026, 4:12:34 AM » (EN) ou « 29 sept. 2026, 04:12:34 » (FR), en UTC."""
    s = s.strip()
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})\s+([^\s\d]+)\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        mon = _MONTHS.get(m.group(2).lower().rstrip("."))
        if mon:
            return datetime(int(m.group(3)), mon, int(m.group(1)), int(m.group(4)), int(m.group(5)),
                            int(m.group(6)), tzinfo=UTC)
    return None


def _num(s: str | None) -> float | None:
    if s is None or not str(s).strip():
        return None
    t = str(s).strip().replace(" ", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_strava_csv(text: str) -> list[dict]:
    """activities.csv : attention, certaines colonnes existent en double
    (« Distance » en km puis en m, « Elapsed Time » deux fois)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    idx: dict[str, list[int]] = {}
    for i, h in enumerate(head):
        idx.setdefault(h.lower(), []).append(i)

    def col(r, *names, occurrence=0):
        for n in names:
            pos = idx.get(n.lower())
            if pos and len(pos) > occurrence and pos[occurrence] < len(r):
                return r[pos[occurrence]]
        return None

    out = []
    for r in rows[1:]:
        start = parse_strava_date(col(r, "Activity Date", "Date de l'activité") or "")
        if not start:
            continue
        dist_m = _num(col(r, "Distance", occurrence=1))
        if dist_m is None:
            km = _num(col(r, "Distance"))
            dist_m = km * 1000 if km is not None else 0.0
        moving = _num(col(r, "Moving Time", "Temps de déplacement"))
        elapsed = _num(col(r, "Elapsed Time", "Temps écoulé"))
        out.append({"start": start, "type": col(r, "Activity Type", "Type d'activité") or "",
                    "name": col(r, "Activity Name", "Nom de l'activité") or "",
                    "moving_s": moving or elapsed or 0.0, "elapsed_s": elapsed or moving or 0.0,
                    "distance_m": dist_m, "dplus_m": _num(col(r, "Elevation Gain", "Dénivelé positif")) or 0.0,
                    "avg_hr": _num(col(r, "Average Heart Rate", "Fréquence cardiaque moyenne")),
                    "filename": (col(r, "Filename", "Nom du fichier") or "").strip()})
    return out


@dataclass
class ImportReport:
    new: int = 0
    upgraded: int = 0
    duplicate: int = 0
    skipped: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)

    def add(self, status: str, source: str) -> None:
        setattr(self, status, getattr(self, status) + 1)
        if status != "duplicate":
            self.by_source[source] = self.by_source.get(source, 0) + 1


def _archive_root(path: Path, tmp: Path) -> Path:
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            members = [m for m in z.namelist()
                       if m.endswith("activities.csv") or "/activities/" in f"/{m}"]
            z.extractall(tmp, members=members)
        path = tmp
    csvs = sorted(path.rglob("activities.csv"), key=lambda p: len(p.parts))
    return csvs[0].parent if csvs else path


def import_archive(path: str | Path, store: PilotStore, st: PilotSettings, days: int = 180,
                   today: date | None = None) -> ImportReport:
    """Importe les `days` derniers jours d'une archive Strava (zip ou dossier décompressé)."""
    today = today or date.today()
    since = today - timedelta(days=days)
    rep = ImportReport()
    with tempfile.TemporaryDirectory() as tmp:
        root = _archive_root(Path(path).expanduser(), Path(tmp))
        csv_path = root / "activities.csv"
        if csv_path.exists():
            rows = parse_strava_csv(csv_path.read_text(encoding="utf-8-sig", errors="replace"))
            for row in rows:
                if row["start"].date() < since:
                    continue
                a = None
                f = root / row["filename"] if row["filename"] else None
                if f and f.exists() and re.search(r"\.fit(\.gz)?$", f.name, re.I):
                    try:
                        a = activity_from_file(f, st, "archive Strava")
                        a.sport, a.name = sport_label(row["type"]), row["name"]
                    except Exception as e:                    # fichier abîmé : on garde le CSV
                        rep.skipped.append(f"{f.name}: {e} (repli sur le CSV)")
                if a is None:
                    sport = sport_label(row["type"])
                    load, src = load_from_avg_hr(row["avg_hr"], row["moving_s"], sport, st)
                    a = Activity(row["start"], _local_day(row["start"], st.tz), sport,
                                 effective_duration(row["elapsed_s"], row["moving_s"]),
                                 row["distance_m"], row["dplus_m"], row["avg_hr"], load, src,
                                 "archive Strava", row["name"], row["moving_s"] or None)
                rep.add(store.add_activity(a), a.load_source)
        else:                                                 # pas de CSV : on lit les fichiers
            for f in sorted(root.rglob("*")):
                if not re.search(r"\.(fit|gpx)(\.gz)?$", f.name, re.I):
                    continue
                try:
                    a = activity_from_file(f, st, "fichier")
                except Exception as e:
                    rep.skipped.append(f"{f.name}: {e}")
                    continue
                if a.local_day >= since:
                    rep.add(store.add_activity(a), a.load_source)
    return rep
