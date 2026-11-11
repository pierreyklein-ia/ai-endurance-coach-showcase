"""Faux service Google Calendar (même chaînage : svc.events().list(...).execute())."""
from __future__ import annotations

import itertools


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Events:
    def __init__(self, svc: Svc):
        self.svc = svc

    def list(self, calendarId, privateExtendedProperty=None, showDeleted=False, maxResults=250,
             pageToken=None, singleEvents=False, **kw):
        def run():
            items = list(self.svc.data.values())
            if privateExtendedProperty:
                k, v = privateExtendedProperty.split("=", 1)
                items = [e for e in items if e.get("extendedProperties", {}).get("private", {}).get(k) == v]
            return {"items": items[:maxResults]}
        self.svc.calls.append(("list", privateExtendedProperty))
        return _Req(run)

    def insert(self, calendarId, body):
        def run():
            eid = f"ev{next(self.svc.ids)}"
            self.svc.data[eid] = {**body, "id": eid}
            return self.svc.data[eid]
        self.svc.calls.append(("insert", body["summary"]))
        return _Req(run)

    def patch(self, calendarId, eventId, body):
        def run():
            self.svc.data[eventId].update(body)
            return self.svc.data[eventId]
        self.svc.calls.append(("patch", eventId))
        return _Req(run)

    def delete(self, calendarId, eventId):
        def run():
            self.svc.data.pop(eventId)
            return {}
        self.svc.calls.append(("delete", eventId))
        return _Req(run)


class Svc:
    def __init__(self):
        self.data: dict[str, dict] = {}
        self.calls: list = []
        self.ids = itertools.count(1)

    def events(self):
        return _Events(self)

    def add_personal(self, summary: str) -> str:
        eid = f"perso{next(self.ids)}"
        self.data[eid] = {"id": eid, "summary": summary}
        return eid

    def coach_events(self) -> list[dict]:
        return [e for e in self.data.values()
                if e.get("extendedProperties", {}).get("private", {}).get("coach_app")]
