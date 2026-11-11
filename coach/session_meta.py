"""v0.50.1 — Titre, type et texte d'une séance toujours d'accord avec son intensité réelle.

Bug corrigé : quand le coach transformait une séance (ex. « Seuil 3 × 8 min » remplacée par un footing),
l'intensité et le texte changeaient mais le titre, le type (« kind ») et le badge « clé » gardés à part
(`session_meta`) restaient ceux d'origine. Avant la v0.50, la carte affichait même l'ancien détail : l'athlète
voyait « Seuil 3 × 8 min », courait son seuil, et la validation automatique comparait au footing (« plus dur
que prévu », séance « modifiée »).

Désormais : à chaque changement d'intensité, le titre, le type et le badge suivent la séance réelle, et
l'origine est gardée (`orig` : titre et type prévus au départ) pour l'afficher (« prévu au départ : Seuil ») et
pour accepter, à la validation, la séance d'origine si c'est elle que l'athlète a faite. Fonctions pures.
"""
from __future__ import annotations

KEY_INTENSITIES = ("vo2", "threshold", "long", "race", "hills", "tempo")
SKIP = ("rest", "race")
INTENSITIES = ("z1", "z2", "tempo", "threshold", "vo2", "long", "hills")   # pas les types du carnet (« ef »…)


def _title(s) -> str:
    from .onboarding import TITLES, session_title
    try:
        return session_title(s)
    except Exception:                                   # noqa: BLE001 : texte libre du coach
        return TITLES.get(s.intensity, s.intensity)


def sync(meta: dict, old, new) -> bool:
    """Met à jour `meta` (dict des séances) si l'intensité de la séance a changé. Renvoie True si modifié."""
    if old is None or old.intensity == new.intensity or old.intensity in SKIP or new.intensity in SKIP \
            or new.is_race or old.is_race:
        return False
    m = dict(meta.get(new.session_id) or {})
    if m.get("kind") in ("renfo", "mobilite"):
        return False
    k = m.get("kind")
    orig = m.get("orig") or {"title": m.get("title") or _title(old), "kind": k if k in INTENSITIES else old.intensity}
    if orig.get("kind") == new.intensity:               # retour à la séance d'origine
        m.pop("orig", None)
    else:
        m["orig"] = orig
    m.update({"title": _title(new), "kind": new.intensity, "detail": new.description,
              "key": new.intensity in KEY_INTENSITIES})
    meta[new.session_id] = m
    return True


def stale(meta: dict, s) -> bool:
    """Séance dont le titre / type gardés à part ne correspondent plus à l'intensité (données d'avant le correctif)."""
    m = meta.get(s.session_id) or {}
    k = m.get("kind")
    return k in INTENSITIES and k != s.intensity and s.intensity not in SKIP and not s.is_race


def orig_kind(meta: dict, s) -> str | None:
    """Type prévu au départ, s'il diffère de l'intensité actuelle."""
    m = meta.get(s.session_id) or {}
    k = (m.get("orig") or {}).get("kind") or m.get("kind")
    return k if k in INTENSITIES and k != s.intensity else None


__all__ = ["orig_kind", "stale", "sync"]
