"""v0.25 : repli quand Claude est indisponible, image d'avatar (Gemini), robustesse remise à 0 après un arrêt."""
import base64
from datetime import timedelta

import pytest

from coach import avatar_image as ai
from coach.llm import FallbackLLM
from coach.radar import _robustness
from coach.sandbox import HeuristicLLM
from coach.schemas import Signals
from tests.test_onboarding import TODAY, act, chat, long_runner, make


class Boom:
    usage_log = []
    client = None

    def structured(self, **kw):
        raise RuntimeError("Error code: 429 - rate_limit_error")


def test_fallback_llm_answers_offline_when_claude_is_limited():
    llm = FallbackLLM(Boom(), HeuristicLLM(TODAY))
    out = llm.structured(node="extract", tier="small", system="s", user="Message: mal au genou 6/10", schema=Signals)
    assert out.pain and llm.failures == 1 and "limite" in llm.degraded


def test_chat_says_when_reply_is_simplified(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km"); chat(c, "Oui, c'est ça"); chat(c, "5 jours. Repos le lundi."); chat(c, "Pas de limite"); chat(c, "Ça me va")
    llm = c.pilot.deps.llm
    c.pilot.deps.llm = FallbackLLM(Boom(), llm)
    c.pilot.deps.llm.usage_log = llm.usage_log
    r = chat(c, "J'ai les jambes lourdes aujourd'hui, je fais quoi ?")
    txt = r["messages"][-1]["text"]
    assert "Réponse simplifiée" in txt and "limite" in txt and "Erreur interne" not in txt


def test_robustness_resets_after_two_weeks_off_and_regains_a_point_per_month():
    runs = []
    for k in range(200):                                       # 1 sortie sur 2 jours pendant 400 jours
        runs.append(act(TODAY - timedelta(days=2 * k + 1), 10, 55))
    full = _robustness(runs, None, TODAY)
    assert full["score"] == 10
    stop = TODAY - timedelta(days=95)                          # arrêt de 20 jours il y a ~3 mois
    hurt = [a for a in runs if not (stop - timedelta(days=20) < a.local_day < stop)]
    r = _robustness(hurt, None, TODAY)
    assert 2.5 <= r["score"] <= 3.5 and "repris" in r["basis"]
    # récupération prévue après une très longue course : pas une blessure
    ultra = hurt + [act(stop - timedelta(days=21), 160, 30 * 60)]
    assert _robustness(ultra, None, TODAY)["score"] == 10
    # arrêt en cours
    assert _robustness([a for a in runs if a.local_day < TODAY - timedelta(days=15)], None, TODAY)["score"] == 0


def test_gemini_response_parsing_and_prompt():
    png = b"\\x89PNG fake"
    js = {"candidates": [{"content": {"parts": [{"text": "ok"}, {"inlineData": {"mimeType": "image/png",
                                                                                  "data": base64.b64encode(png).decode()}}]}}]}
    assert ai.extract_image(js) == png
    with pytest.raises(RuntimeError):
        ai.extract_image({"promptFeedback": {"blockReason": "SAFETY"}})
    p = ai.prompt({"skin": 2, "hair": "long", "hair_color": 0, "shirt": 1, "shorts": 0, "shoes": 3, "beard": True},
                  24, ["casquette", "gilet", "dossard"], "montagne", "trail")
    assert "ponytail" in p and "trail running vest" in p and "number 24" in p and "no brand logos" in p
    assert "golden stars" in p


def test_avatar_image_generated_once_per_change(tmp_path, monkeypatch):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    r = c.get("/api/career").json()
    assert r["avatar_image"]["enabled"] is False                # sans clé : avatar dessiné
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []
    monkeypatch.setattr(ai, "generate", lambda text, ref=None, face=None: calls.append((text, ref)) or b"PNG1")
    r = c.get("/api/career").json()
    assert r["avatar_image"]["ready"] and len(calls) == 1 and calls[0][1] is None
    assert c.get(r["avatar_image"]["url"]).content == b"PNG1"
    c.get("/api/career")
    assert len(calls) == 1                                     # rien n'a changé : pas de nouvelle image
    c.post("/api/career/avatar", json={"skin": 4, "hair": "boucles"})
    r = c.get("/api/career").json()
    assert len(calls) == 1 and r["avatar_image"]["next_at"]    # v0.30 : une image au plus toutes les 48 h
    c.post("/api/career/avatar/regenerate")
    assert len(calls) == 1                                     # même en le demandant
    st = c.pilot.store.get("avatar_image")
    c.pilot.store.set("avatar_image", {**st, "at": "2020-01-01T00:00:00+00:00"})   # 48 h plus tard
    c.get("/api/career")
    assert len(calls) == 2 and calls[1][1] == b"PNG1"           # même personnage : image précédente en référence
