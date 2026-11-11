"""Dictée (v0.21) : transcription de la voix sur le serveur, sans service externe.

Le bouton micro de l'app utilise d'abord la reconnaissance vocale du navigateur (texte en direct). Quand elle
n'existe pas ou échoue (iPhone, app installée sur l'écran d'accueil), l'app enregistre la voix et l'envoie ici :
Whisper (faster-whisper, modèle libre, CPU, int8) la transcrit sur le serveur. L'audio n'est jamais gardé.

Réglages (deploy/app.env) :
- COACH_STT=off            désactive la dictée serveur (le bouton n'utilise alors que le navigateur) ;
- COACH_STT_MODEL=small    base (plus rapide) | small (défaut, meilleur en français) | medium (lent) ;
- COACH_STT_DIR=/models    où le modèle est téléchargé au 1er démarrage (hors des sauvegardes).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

log = logging.getLogger(__name__)

MAX_BYTES = 6 * 1024 * 1024          # ~3 min d'audio compressé
MAX_SECONDS = 180
# vocabulaire de l'app : aide Whisper à écrire juste les termes de course et de musculation
VOCAB = ("Séance de seuil, fractionné, VMA, sortie longue, endurance fondamentale, D+, trail, ultra, "
         "allure, FC, cardio, renfo, box squat, trap bar, Nordic curl, soulevé de terre roumain, pliométrie, "
         "ischios, mollets, tendon d'Achille, Strava, Diagonale des Fous, VVX, km, semi-marathon.")


def _whisper_factory(name: str, root: str):
    """Fabrique de production : le vrai modèle faster-whisper (CPU, int8)."""
    from faster_whisper import WhisperModel
    threads = max(1, min(4, os.cpu_count() or 1))
    return WhisperModel(name, device="cpu", compute_type="int8", download_root=root, cpu_threads=threads)


def _daemon(target: Callable[[], object]) -> None:
    threading.Thread(target=target, daemon=True, name="stt-warmup").start()


class Transcriber:
    """Inversion de contrôle (tests) : `env` (variables d'environnement), `model_factory(nom, dossier)` qui fabrique
    le modèle (faster-whisper par défaut) et `spawn(fonction)` qui lance le préchargement (fil de fond par défaut)."""

    def __init__(self, model: str | None = None, root: str | Path | None = None, enabled: bool | None = None, *,
                 env: Mapping[str, str] | None = None, model_factory: Callable[[str, str], object] | None = None,
                 spawn: Callable[[Callable[[], object]], None] = _daemon):
        env = os.environ if env is None else env
        self.model_name = model or env.get("COACH_STT_MODEL", "small")
        self.root = str(root or env.get("COACH_STT_DIR") or Path.home() / ".cache" / "coach-stt")
        self.enabled = (env.get("COACH_STT", "on").lower() not in ("off", "0", "non", "no")
                        if enabled is None else enabled)
        self._factory, self._spawn = model_factory, spawn
        self._model = None
        self._lock = threading.Lock()
        self._error: str | None = None
        self._loading = False

    # ------------------------------------------------------------------ état
    def available(self) -> bool:
        if not self.enabled or self._error:
            return False
        if self._factory is not None:
            return True
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def status(self) -> dict:
        return {"server": self.available(), "ready": self._model is not None, "model": self.model_name,
                "loading": self._loading}

    def warm_up(self) -> None:
        """Télécharge et charge le modèle en tâche de fond, pour que la 1re dictée ne soit pas lente."""
        if self.available() and self._model is None and not self._loading:
            self._spawn(self._load)

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            self._loading = True
            try:
                t0 = time.monotonic()
                Path(self.root).mkdir(parents=True, exist_ok=True)
                self._model = (self._factory or _whisper_factory)(self.model_name, self.root)
                log.info("dictée : modèle %s prêt en %.0f s", self.model_name, time.monotonic() - t0)
            except Exception as e:                                   # pas de réseau, disque plein…
                self._error = str(e)[:300]
                log.exception("dictée : modèle %s indisponible", self.model_name)
            finally:
                self._loading = False
            return self._model

    # ------------------------------------------------------------------ transcription
    def transcribe(self, data: bytes, suffix: str = ".webm") -> str:
        if not self.available():
            raise RuntimeError("dictée serveur indisponible")
        if not data:
            raise ValueError("enregistrement vide")
        if len(data) > MAX_BYTES:
            raise ValueError("enregistrement trop long (3 min au plus)")
        model = self._model or self._load()
        if model is None:
            raise RuntimeError("dictée serveur indisponible")
        with tempfile.NamedTemporaryFile(suffix=suffix) as f:
            f.write(data)
            f.flush()
            with self._lock:                                         # une transcription à la fois (2 cœurs)
                segments, info = model.transcribe(f.name, language="fr", initial_prompt=VOCAB, beam_size=1,
                                                  vad_filter=True, condition_on_previous_text=False)
                parts = []
                for s in segments:
                    if s.start > MAX_SECONDS:
                        break
                    parts.append(s.text.strip())
        return clean(" ".join(p for p in parts if p))


def clean(text: str) -> str:
    """Espaces normalisés ; Whisper recopie parfois le vocabulaire du prompt sur un silence : on l'écarte."""
    t = " ".join(text.split())
    if t and t.rstrip(".").strip() in VOCAB.rstrip("."):
        return ""
    return t


SUFFIX = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4", "audio/aac": ".aac",
          "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-m4a": ".m4a"}


def suffix_for(content_type: str | None) -> str:
    return SUFFIX.get((content_type or "").split(";")[0].strip().lower(), ".webm")
