from datetime import date, timedelta

import pytest

from coach.adapters.calendar import InMemoryCalendar
from coach.graph import AthleteContext, Deps
from coach.load_engine import LoadState
from coach.rag import SEED_CORPUS, TagRetriever
from coach.schemas import PlannedSession

TODAY = date(2026, 9, 23)
RACE = date(2026, 10, 15)   # Diagonale des Fous 2026 (départ le 15/10)


def d(i):
    return TODAY + timedelta(days=i)


PLAN = [
    PlannedSession(session_id="s1", day=d(0), intensity="threshold", duration_min=70, planned_load=85),
    PlannedSession(session_id="s2", day=d(1), intensity="z2", duration_min=60, planned_load=50),
    PlannedSession(session_id="s3", day=d(2), intensity="rest", duration_min=0, planned_load=0),
    PlannedSession(session_id="s4", day=d(3), intensity="long", duration_min=240,
                   elevation_gain_m=2000, planned_load=220),
    PlannedSession(session_id="s5", day=d(4), intensity="z2", duration_min=90, planned_load=70),
    PlannedSession(session_id="s6", day=d(5), intensity="rest", duration_min=0, planned_load=0),
    PlannedSession(session_id="s7", day=d(6), intensity="hills", duration_min=75, planned_load=90),
]


class FakeRepo:
    def get(self, athlete_id):
        return AthleteContext(
            athlete_id=athlete_id, race_date=RACE,
            load=LoadState(ctl=95, atl=100, tsb=-5, acwr=1.05),
            plan_next_7d=PLAN, baseline_week_load=sum(p.planned_load for p in PLAN),
            profile_summary="Ultra-traileur expérimenté, tendance à en faire trop.",
        )


@pytest.fixture
def make_deps():
    def _make(llm):
        return Deps(llm=llm, retriever=TagRetriever(SEED_CORPUS),
                    calendar=InMemoryCalendar(), repo=FakeRepo(), today=lambda: TODAY)
    return _make
