"""Petits restes : lancement du bot de test (variables d'environnement), transport urllib, chargeur du calibrage."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest

from coach.adapters import telegram_bot as tb
from coach.pacer import calibrate
from tests.fitwriter import write_fit


def test_urllib_transport_posts_json_and_reads(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        seen["timeout"] = timeout
        if isinstance(req, str):
            return Resp(b"FICHIER")
        seen["body"], seen["ct"] = json.loads(req.data), req.headers["Content-type"]
        return Resp(b'{"ok": true, "result": 1}')

    monkeypatch.setattr(tb.urllib.request, "urlopen", urlopen)
    assert tb._urllib_post("https://t/x", {"a": 1}, 5) == {"ok": True, "result": 1}
    assert seen == {"timeout": 5, "body": {"a": 1}, "ct": "application/json"}
    assert tb._urllib_get("https://t/f", 9) == b"FICHIER"


def test_main_refuses_without_token_or_key(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert tb.main([]) == 1
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert tb.main(["--claude"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_main_starts_bot_with_whitelist(monkeypatch, capsys):
    started = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    monkeypatch.setattr(tb.CoachBot, "run_forever", lambda self: started.append(set(self.allowed)))
    assert tb.main([]) == 0 and started == [set()] and "liste blanche" in capsys.readouterr().out
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1, 2")
    assert tb.main([]) == 0 and started[-1] == {1, 2}


def test_load_activity_formats(tmp_path):
    write_fit(tmp_path / "a.fit", datetime(2026, 9, 1, 6, tzinfo=UTC), 600, lambda i: 140)
    assert calibrate.load_activity(tmp_path / "a.fit").t is not None
    (tmp_path / "s.json").write_text(json.dumps({"time": [0, 10], "distance": [0, 30], "altitude": [1, 2]}))
    assert calibrate.load_activity(tmp_path / "s.json").name == "s"
    (tmp_path / "t.gpx").write_text('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
                                    '<trk><trkseg><trkpt lat="45" lon="3"><ele>1</ele></trkpt>'
                                    '<trkpt lat="45.01" lon="3"><ele>2</ele></trkpt></trkseg></trk></gpx>')
    with pytest.raises(ValueError, match="horodatage"):
        calibrate.load_activity(tmp_path / "t.gpx")
    with pytest.raises(ValueError, match="format"):
        calibrate.load_activity(tmp_path / "x.csv")


def test_calibrate_main_with_few_heart_rates_and_backtest(tmp_path, capsys):
    from datetime import timedelta

    import gpxpy.gpx

    from tests.test_predict_cli import training_set
    folder = tmp_path / "acts"
    folder.mkdir()
    for tr in training_set()[:6]:
        g = gpxpy.gpx.GPX()
        t = gpxpy.gpx.GPXTrack()
        g.tracks.append(t)
        s = gpxpy.gpx.GPXTrackSegment()
        t.segments.append(s)
        for i in range(len(tr.dist)):
            s.points.append(gpxpy.gpx.GPXTrackPoint(47.26 + tr.dist[i] / 111195.0, -1.28, elevation=float(tr.alt[i]),
                                                    time=tr.start_utc + timedelta(seconds=float(tr.t[i]))))
        (folder / f"{tr.name}.gpx").write_text(g.to_xml())
    (folder / "notes.txt").write_text("x")
    out = tmp_path / "p.json"
    assert calibrate.main(["--activities", str(folder), "--out", str(out), "--hr-rest", "45", "--hr-max", "190",
                           "--backtest"]) == 0
    txt = capsys.readouterr().out
    assert "1 ignorées" in txt and "moins de 3 activités avec FC" in txt and "moins de 2 courses" in txt
