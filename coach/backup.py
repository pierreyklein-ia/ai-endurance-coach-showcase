"""Sauvegarde des données de l'application (v0.16).

    python -m coach.backup                 # dans le conteneur : COACH_HOME=/data
    docker compose exec -T app python -m coach.backup     # depuis le serveur (cron quotidien, voir setup.sh)

Ce qui est sauvegardé : tout COACH_HOME (bases SQLite, jetons Strava, clés des notifications, réglages),
sauf le dossier des sauvegardes lui-même. Les bases SQLite sont copiées avec l'API de sauvegarde de SQLite :
copie cohérente même si l'application écrit au même moment.

Où :
- une archive `coach-AAAAMMJJ-HHMM.tar.gz` dans COACH_HOME/backups (les 14 dernières sont gardées) ;
- et, si COACH_BACKUP_URL est défini, une copie envoyée vers Oracle Object Storage. COACH_BACKUP_URL est une
  « pre-authenticated request » en écriture seule sur un bucket privé (elle permet de déposer, pas de lire ni
  de lister) ; l'archive est ajoutée au bout de l'URL.

Restauration : deploy/restore.sh (arrête l'application, remplace les données, redémarre).
"""
from __future__ import annotations

import io
import logging
import os
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

log = logging.getLogger("coach.backup")
KEEP = 14
SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_sqlite(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def snapshot(home: Path, out_dir: Path, now: datetime | None = None) -> Path:
    """Crée l'archive et renvoie son chemin."""
    now = now or datetime.now()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"coach-{now:%Y%m%d-%H%M}.tar.gz"
    target = out_dir / name
    tmp = target.with_suffix(".part")
    with tempfile.TemporaryDirectory() as td, tarfile.open(tmp, "w:gz") as tar:
        for p in sorted(home.rglob("*")):
            rel = p.relative_to(home)
            if not p.is_file() or rel.parts[0] in (out_dir.name, "private") or p.name.endswith(("-wal", "-shm", "-journal")):
                # v0.34 : private/ (photo du visage) ne quitte jamais le serveur, même dans les sauvegardes
                continue
            if _is_sqlite(p):
                copy = Path(td) / rel.as_posix().replace("/", "__")
                src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                dst = sqlite3.connect(copy)
                with dst:
                    src.backup(dst)
                src.close()
                dst.close()
                tar.add(copy, arcname=str(rel))
            else:
                tar.add(p, arcname=str(rel))
        meta = f"créé le {now.isoformat(timespec='seconds')}\n".encode()
        info = tarfile.TarInfo("BACKUP_INFO.txt")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    tmp.rename(target)
    target.chmod(0o600)
    return target


def prune(out_dir: Path, keep: int = KEEP) -> list[Path]:
    old = sorted(out_dir.glob("coach-*.tar.gz"))[:-keep] if keep > 0 else []
    for p in old:
        p.unlink()
    return old


def upload(path: Path, url: str, http=None) -> int:
    """Dépose l'archive via la pre-authenticated request (PUT). Retourne le code HTTP."""
    import httpx
    http = http or httpx.put
    full = url.rstrip("/") + "/" + path.name
    with path.open("rb") as f:
        r = http(full, content=f.read(), headers={"Content-Type": "application/gzip"}, timeout=120)
    return r.status_code


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, http=None) -> int:
    """`env` (os.environ par défaut) et `http` (httpx.put par défaut) sont injectables pour les tests."""
    env = os.environ if env is None else env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    home = Path(env.get("COACH_HOME", Path.home() / ".ai_coach_app"))
    out_dir = home / "backups"
    path = snapshot(home, out_dir)
    size = path.stat().st_size / 1024
    removed = prune(out_dir)
    print(f"sauvegarde locale : {path.name} ({size:.0f} Ko) ; {len(removed)} ancienne(s) supprimée(s)")
    url = env.get("COACH_BACKUP_URL", "").strip()
    if not url:
        print("copie distante : COACH_BACKUP_URL non défini (seulement la copie locale)")
        return 0
    try:
        code = upload(path, url, http)
    except Exception as e:                       # noqa: BLE001 (réseau)
        print(f"ÉCHEC de la copie distante : {type(e).__name__} : {str(e)[:200]}", file=sys.stderr)
        return 2
    if code >= 300:
        print(f"ÉCHEC de la copie distante : HTTP {code} (URL expirée ou révoquée ?)", file=sys.stderr)
        return 2
    print(f"copie distante : OK (Object Storage, HTTP {code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
