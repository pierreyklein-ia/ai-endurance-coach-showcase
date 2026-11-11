"""v0.36 — coach personnalisable : genre, personnalité, prénom, tu / vous (ton seulement, jamais la sécurité)."""
import dataclasses
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from coach.persona import KEY, CoachPersona, persona_block
from coach.pilot.runtime import build_pilot
from coach.sandbox import HeuristicLLM
from coach.web.app import create_app
from coach.web.service import CoachService
from tests import test_onboarding as tob
from tests import test_web as tw
from tests.test_pilot import ST


@dataclass
class Recorder:
    """Enveloppe le LLM hors ligne et garde chaque prompt système envoyé, par nœud."""
    inner: HeuristicLLM
    systems: list[tuple[str, str]] = field(default_factory=list)

    @property
    def usage_log(self):
        return self.inner.usage_log

    def structured(self, *, node, tier, system, user, schema):
        self.systems.append((node, system))
        return self.inner.structured(node=node, tier=tier, system=system, user=user, schema=schema)


# --------------------------------------------------------------------------- modèle
def test_defaults_and_validation():
    p = CoachPersona()
    assert (p.gender, p.personality, p.name, p.address) == ("femme", "bienveillant", "Léa", "tu")
    assert CoachPersona(name="  <b>Jo</b>; ").name == "bJob"            # balises et ponctuation retirées
    assert CoachPersona(name="Jean-Luc O'Neil").name == "Jean-Luc O'Neil"
    for bad in ({"gender": "robot"}, {"personality": "méchant"}, {"address": "il"}, {"name": ""},
                {"name": "<>;"}, {"name": "x" * 21}):
        with pytest.raises(ValidationError):
            CoachPersona(**bad)


def test_block_states_tone_only_and_safety_unchanged():
    b = persona_block(CoachPersona(gender="femme", personality="exigeant", name="Nina", address="vous"))
    assert "Nina" in b and "je suis prête" in b and "VOUVOIE" in b
    assert "JAMAIS sur la douleur" in b and "restent strictement identiques" in b
    m = persona_block(CoachPersona(gender="homme", personality="sante", name="Marc"))
    assert "je suis prêt »" in m and "TUTOIE" in m and "ne modifie pas le plan" in m


# --------------------------------------------------------------------------- API
def test_api_round_trip_and_me_field(tmp_path):
    c = tw.make(tmp_path)
    g = c.get("/api/coach").json()
    assert g["name"] == "Léa" and g["chosen"] is False
    assert c.get("/api/me").json()["coach"]["name"] == "Léa"
    body = {"gender": "homme", "personality": "motivateur", "name": " Marco ", "address": "vous"}
    r = c.post("/api/coach", json=body)
    assert r.status_code == 200 and r.json() == {**body, "name": "Marco", "chosen": True, "vocabulary": "simple"}  # v0.48
    assert c.pilot.store.get(KEY)["name"] == "Marco"
    me = c.get("/api/me").json()["coach"]
    assert me["personality"] == "motivateur" and me["chosen"]
    assert c.post("/api/coach", json={**body, "personality": "brutal"}).status_code == 422
    assert c.post("/api/coach", json={**body, "extra": 1}).status_code == 422
    assert c.post("/api/coach", json={**body, "name": "x" * 30}).status_code == 422
    (tmp_path / "x").mkdir()
    assert tw.make(tmp_path / "x", logged=False).get("/api/coach").status_code == 401


def test_corrupt_store_falls_back_to_defaults(tmp_path):
    c = tw.make(tmp_path)
    c.pilot.store.set(KEY, {"gender": "??"})
    assert c.get("/api/coach").json()["name"] == "Léa"


# --------------------------------------------------------------------------- prompts
def test_persona_block_in_coach_node_prompt(tmp_path):
    rec = Recorder(HeuristicLLM(tw.TODAY))
    c = tw.make(tmp_path, llm=rec)
    c.pilot.write_plan(c.pilot.repo.propose_taper(tw.TODAY)[0])
    c.post("/api/coach", json={"gender": "homme", "personality": "exigeant", "name": "Marc", "address": "vous"})
    r = c.post("/api/chat", json={"text": "J'ai mal au genou depuis hier, je cours quand même demain ?"})
    assert r.status_code == 200
    coach = [s for n, s in rec.systems if n == "coach"]
    assert coach and all("Tu t'appelles Marc" in s and "VOUVOIE" in s and "garde-fous" in s for s in coach)
    assert all("Personnalité du coach" not in s for n, s in rec.systems if n == "extract")   # extraction : pas de ton


def test_persona_block_in_onboarding_prompts(tmp_path):
    rec = Recorder(HeuristicLLM(tob.TODAY))
    p = build_pilot(home=tmp_path, today_fn=lambda: tob.TODAY, settings=dataclasses.replace(ST), llm=rec)
    service = CoachService(p, connector=tob.FakeConnector(), strava_factory=lambda: tob.FakeStrava(
        tob.trail_runner(big_effort_days_ago=12)), background=False)
    c = TestClient(create_app(service, passcode=tob.CODE, secret=tob.SECRET, background=False))
    assert c.post("/api/login", json={"passcode": tob.CODE}).status_code == 200
    assert c.post("/api/coach", json={"gender": "femme", "personality": "sante", "name": "Léa",
                                      "address": "tu"}).status_code == 200
    assert c.post("/api/strava/connect").json()["status"] == "connected"
    tob.chat(c, "La VVX 85 km")
    tob.chat(c, "Oui, c'est ça")
    nodes = {n for n, _ in rec.systems}
    assert {"onboard_recap", "onboard_goal"} <= nodes
    for n, s in rec.systems:
        if n.startswith("onboard"):
            assert "Tu t'appelles Léa" in s and "je suis prête" in s and "santé d'abord" in s
