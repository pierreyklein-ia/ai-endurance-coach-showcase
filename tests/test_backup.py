import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path

from coach.backup import prune, snapshot, upload


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "data"
    home.mkdir()
    db = sqlite3.connect(home / "pilot.db")
    db.execute("create table t(x)")
    db.executemany("insert into t values (?)", [(i,) for i in range(1000)])
    db.commit()
    db.close()
    (home / "strava_api.json").write_text('{"refresh_token": "abc"}')
    (home / "pilot.db-wal").write_text("ignored")
    return home


def test_snapshot_contains_consistent_db_and_files(tmp_path):
    home = _home(tmp_path)
    live = sqlite3.connect(home / "pilot.db")          # une connexion ouverte en écriture pendant la copie
    live.execute("insert into t values (-1)")
    arch = snapshot(home, home / "backups", datetime(2026, 10, 29, 3, 17))
    live.commit()
    assert arch.name == "coach-20261029-0317.tar.gz" and oct(arch.stat().st_mode)[-3:] == "600"
    with tarfile.open(arch) as tar:
        names = set(tar.getnames())
        assert {"pilot.db", "strava_api.json", "BACKUP_INFO.txt"} <= names
        assert "pilot.db-wal" not in names and not any(n.startswith("backups") for n in names)
        tar.extract("pilot.db", tmp_path / "restore")
    n = sqlite3.connect(tmp_path / "restore" / "pilot.db").execute("select count(*) from t").fetchone()[0]
    assert n == 1000                                    # l'écriture non validée n'y est pas : copie cohérente


def test_prune_keeps_the_last_ones(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    for i in range(20):
        (d / f"coach-202610{i + 1:02d}-0317.tar.gz").write_text("x")
    removed = prune(d, keep=14)
    assert len(removed) == 6 and len(list(d.glob("*.tar.gz"))) == 14
    assert not (d / "coach-20261001-0317.tar.gz").exists()


def test_upload_appends_the_name_to_the_par_url(tmp_path):
    home = _home(tmp_path)
    arch = snapshot(home, home / "backups")
    calls = []

    class R:
        status_code = 200

    def fake_put(url, content, headers, timeout):
        calls.append((url, len(content), headers["Content-Type"]))
        return R()
    code = upload(arch, "https://objectstorage.eu-paris-1.oraclecloud.com/p/TOKEN/n/ns/b/coach-backups/o/",
                  http=fake_put)
    assert code == 200 and calls[0][0].endswith("/o/" + arch.name) and calls[0][2] == "application/gzip"
