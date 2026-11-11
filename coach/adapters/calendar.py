"""Restitution headless : Google Calendar.

Décisions :
1. Produit : calendrier SECONDAIRE dédié ("Coach IA") créé par l'app ->
   scope minimal `calendar.app.created` + `calendar.freebusy`.
   Pilote : agenda principal (choix de l'athlète) -> `calendar.events.owned`,
   et le code ne touche qu'aux événements étiquetés par le coach.
2. Idempotence : chaque séance porte `extendedProperties.private.coach_session_id`.
   Un rejeu du graphe (retry, reprise de checkpoint) ne crée jamais de doublon.
3. Le calendrier est aussi une ENTRÉE : si l'athlète déplace une séance à la
   main, la synchro incrémentale (syncToken) doit le remonter comme signal.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Protocol

from ..schemas import PlanChange

# Agenda dédié (défaut produit) : l'app ne voit que les agendas qu'elle a créés.
SCOPES_DEDICATED = [
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/calendar.freebusy",
]
# Agenda principal (choix du pilote) : événements des agendas dont tu es propriétaire.
# Le code ne modifie QUE les événements portant l'étiquette coach_app (voir _find).
SCOPES_PRIMARY = ["https://www.googleapis.com/auth/calendar.events.owned"]
SCOPES = SCOPES_DEDICATED
APP_TAG = "ai-endurance-coach"
SESSION_LABELS = {"rest": "Repos", "z1": "Footing Z1", "z2": "Endurance Z2", "tempo": "Tempo",
                  "threshold": "Seuil", "vo2": "VMA", "long": "Sortie longue", "hills": "Côtes",
                  "race": "COURSE"}


class CalendarPort(Protocol):
    def apply(self, athlete_id: str, changes: list[PlanChange]) -> list[str]: ...


def _event_body(ch: PlanChange, tz: str, start_at: time = time(18, 0),
                race_start: datetime | None = None, race_tz: str | None = None, extra: str = "") -> dict:
    s = ch.session
    if s.is_race and race_start is not None:
        start = race_start.replace(tzinfo=None)
        tz = race_tz or tz
        summary = f"🏁 {s.description.split(' — ')[0] or 'Course'}"
    else:
        start = datetime.combine(s.day, start_at)
        summary = (f"🏃 {SESSION_LABELS.get(s.intensity, s.intensity)} · {s.duration_min} min"
                   + (f" · D+ {s.elevation_gain_m} m" if s.elevation_gain_m else ""))
    end = start + timedelta(minutes=max(s.duration_min, 15))
    return {
        "summary": summary,
        "description": (s.description + "\n\n" if s.description else "")
                       + (extra + "\n\n" if extra else "")          # v0.50 : FC cible, allure, effort
                       + "Proposé par AI Endurance Coach — réponds au coach sur Telegram pour l'adapter.",
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
        "transparency": "transparent",          # n'apparaît pas « occupé » pour les autres
        "colorId": "2",
        "extendedProperties": {"private": {"coach_session_id": s.session_id, "coach_app": APP_TAG}},
        "reminders": {"useDefault": False, "overrides": []},  # "silencieux"
    }


class GoogleCalendarAdapter:
    """Écrit les séances dans un agenda Google.

    Garantie : on ne lit/modifie/supprime que des événements retrouvés par leur
    étiquette privée (coach_session_id / coach_app). Tes autres événements ne
    sont jamais touchés, même avec l'accès à l'agenda principal.
    Les jours de repos ne créent pas d'événement (bruit inutile dans l'agenda).
    """

    def __init__(self, service, calendar_id: str = "primary", tz: str = "Europe/Paris",
                 session_hour: int = 18, race_start: datetime | None = None, race_tz: str | None = None):
        self.svc, self.cal, self.tz = service, calendar_id, tz
        self.start_at = time(session_hour, 0)
        self.race_start, self.race_tz = race_start, race_tz
        self.extra_fn = None        # v0.50 : séance -> ligne « Cible : FC … » (posé par l'application)

    def _find(self, session_id: str):
        items = self.svc.events().list(
            calendarId=self.cal,
            privateExtendedProperty=f"coach_session_id={session_id}",
            showDeleted=False, maxResults=1,
        ).execute().get("items", [])
        return items[0] if items else None

    def _body(self, ch: PlanChange) -> dict:
        extra = ""
        if self.extra_fn is not None and not ch.session.is_race:
            try:
                extra = self.extra_fn(ch.session) or ""
            except Exception:                    # noqa: BLE001 : l'événement s'écrit quand même
                extra = ""
        return _event_body(ch, self.tz, self.start_at, self.race_start, self.race_tz, extra)

    def apply(self, athlete_id, changes):
        done = []
        for ch in changes:
            existing = self._find(ch.session.session_id)
            if ch.action == "cancel" or ch.session.intensity == "rest":
                if existing:
                    self.svc.events().delete(calendarId=self.cal, eventId=existing["id"]).execute()
                    done.append(f"deleted:{ch.session.session_id}")
            elif ch.action == "keep":
                continue
            elif existing:
                self.svc.events().patch(calendarId=self.cal, eventId=existing["id"],
                                        body=self._body(ch)).execute()
                done.append(f"patched:{ch.session.session_id}")
            else:
                self.svc.events().insert(calendarId=self.cal, body=self._body(ch)).execute()
                done.append(f"inserted:{ch.session.session_id}")
        return done

    def coach_events(self) -> list[dict]:
        out, token = [], None
        while True:
            resp = self.svc.events().list(calendarId=self.cal, privateExtendedProperty=f"coach_app={APP_TAG}",
                                          showDeleted=False, maxResults=250, pageToken=token,
                                          singleEvents=True).execute()
            out += resp.get("items", [])
            token = resp.get("nextPageToken")
            if not token:
                return out

    def clear_all(self) -> int:
        """Supprime TOUT ce que le coach a écrit (et rien d'autre)."""
        evs = self.coach_events()
        for e in evs:
            self.svc.events().delete(calendarId=self.cal, eventId=e["id"]).execute()
        return len(evs)


class InMemoryCalendar:
    def __init__(self):
        self.events: dict[str, dict] = {}

    def apply(self, athlete_id, changes):
        done = []
        for ch in changes:
            sid = ch.session.session_id
            if ch.action == "cancel":
                self.events.pop(sid, None)
                done.append(f"deleted:{sid}")
            elif ch.action != "keep":
                verb = "patched" if sid in self.events else "inserted"
                self.events[sid] = _event_body(ch, "Europe/Paris")
                done.append(f"{verb}:{sid}")
        return done
