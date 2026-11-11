"""Recherche d'une course (date, distance, D+) pour la faire confirmer à l'athlète.

1. Recherche web par Claude (outil serveur `web_search` de l'API Anthropic), si l'application tourne
   avec Claude ET que la recherche web est activée dans la console Anthropic. Coût : ~1 centime par
   recherche + les jetons. Le résultat est TOUJOURS montré à l'athlète, qui confirme ou corrige.
2. Sinon, catalogue local (data/races/catalog.json) : courses déjà vérifiées, 0 €, hors ligne.
3. Sinon : on demande la date à l'athlète.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger("coach.races")
CATALOG = Path(__file__).resolve().parents[1] / "data" / "races" / "catalog.json"
WEB_SEARCH_EUR = 0.0086           # 10 $ / 1000 recherches


@dataclass
class Found:
    name: str
    date: date
    distance_km: float | None = None
    dplus_m: int | None = None
    kind: str = "trail"
    source: str = ""
    cost_eur: float = 0.0
    start: str = ""                    # v0.48 : heure (et lieu) de départ du format
    options: list[Found] = field(default_factory=list)   # v0.48 : formats possibles quand le format est ambigu

    @property
    def ambiguous(self) -> bool:
        return bool(self.options)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in s if not unicodedata.combining(c)))


# mots trop communs pour identifier une course (« trail patrimoine » ne doit pas désigner le Trail des Vallées)
_GENERIC = {"trail", "course", "de", "du", "des", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux", "the",
            "km", "run", "ultra", "mon", "ma", "objectif", "c", "est", "je", "fais"}


def stated_distance(text: str) -> float | None:
    """« 21 km », « 21,5 km », « 85km » dans un nom de course."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|k)\b", text.lower())
    return float(m[1].replace(",", ".")) if m else None


def _dist_ok(r: dict, km: float) -> bool:
    lo, hi = r.get("distance_range") or [r.get("distance_km"), r.get("distance_km")]
    if lo is None:
        return False
    tol = max(1.5, 0.06 * hi)
    return lo - tol <= km <= hi + tol


def _found(r: dict, source: str = "catalogue de courses") -> Found:
    return Found(r["name"], date.fromisoformat(r["date"]), r.get("distance_km"), r.get("dplus_m"),
                 r.get("kind", "trail"), source, start=r.get("start", ""))


def _catalog(today: date, year: int | None) -> list[dict]:
    if not CATALOG.exists():
        return []
    out = []
    for r in json.loads(CATALOG.read_text()):
        d = date.fromisoformat(r["date"])
        if d > today and (not year or d.year == year):
            out.append(r)
    return out


def from_catalog(name: str, distance_km: float | None, today: date, year: int | None = None) -> Found | None:
    """Course vérifiée du catalogue (v0.48 : un événement de plusieurs jours a un format par jour).

    - mots propres au format (« patrimoine », « batignolles », « pierre de lave ») et distance donnée priment ;
    - une distance donnée qui ne correspond pas au format l'exclut (« VVX 21 km » n'est jamais le 85 km) ;
    - événement reconnu sans format identifiable : `options` liste les formats (on demande), jamais le 1er jour."""
    words = {w for w in _norm(name).split() if w not in _GENERIC and not w.isdigit()}
    km = distance_km or stated_distance(name)
    scored: list[tuple[int, dict, bool]] = []
    for r in _catalog(today, year):
        ev_keys = set(_norm(" ".join(r.get("event_aliases", []))).split())
        own = {w for w in _norm(" ".join([r["name"], *r.get("aliases", [])])).split()
               if w not in _GENERIC and not w.isdigit()} - ev_keys
        ev_hit, own_hit = len(words & ev_keys), len(words & own)
        if not ev_hit and not own_hit:
            continue
        dist = km is not None and _dist_ok(r, km)
        if km is not None and not dist:
            continue                                    # distance donnée : jamais un autre format
        specific = bool(own_hit or dist or not r.get("event"))
        scored.append((2 * own_hit + ev_hit + (3 if dist else 0), r, specific))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    score, best, specific = scored[0]
    if specific and score >= 2:
        return _found(best)
    if not best.get("event") or score < 1:
        return None
    # événement reconnu, format inconnu : on propose les formats (dans l'ordre des jours)
    same = sorted((r for _, r, _ in scored if r.get("event") == best["event"]), key=lambda r: (r["date"],
                                                                                            r.get("distance_km") or 0))
    if len(same) == 1:
        return _found(same[0])
    head = _found(same[0])
    head.options = [_found(r) for r in same]
    return head


def catalog_event(name: str, today: date, year: int | None = None) -> list[dict]:
    """Formats d'un événement du catalogue reconnu par son nom (vide si inconnu)."""
    words = {w for w in _norm(name).split() if w not in _GENERIC and not w.isdigit()}
    evs = {r["event"] for r in _catalog(today, year) if r.get("event")
           and words & set(_norm(" ".join(r.get("event_aliases", []))).split())}
    return [r for r in _catalog(today, year) if r.get("event") in evs]


def options_text(f: Found) -> str:
    """Question posée quand l'événement a plusieurs formats."""
    from .onboarding import JOURS_ABR
    lines = [f"• {o.name.split(' — ')[-1]} : {JOURS_ABR[o.date.weekday()]} {o.date.day} "
             f"{['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'][o.date.month - 1]}"
             + (f", départ {o.start}" if o.start else "") for o in f.options]
    return "Cet événement a plusieurs formats, sur plusieurs jours. Lequel fais-tu (le nom ou la distance suffit) ?\n" \
        + "\n".join(lines)


SEARCH_PROMPT = """Trouve {edition} de cette course : « {name} »{extra}.
Cherche la page officielle. Réponds UNIQUEMENT par un objet JSON :
{{"found": true|false, "name": "nom officiel et format", "date": "AAAA-MM-JJ" (jour du départ de ce format),
"distance_km": nombre, "dplus_m": nombre, "kind": "trail"|"route", "url": "page source"}}
Si plusieurs formats existent, prends celui dont la distance correspond le mieux ({dist}). Un événement s'étale
souvent sur plusieurs jours (ex. du jeudi au samedi) : la date est celle du DÉPART DE CE FORMAT dans le programme,
JAMAIS le premier jour de l'événement. Si tu ne trouves pas le jour précis de ce format, mets found=false."""


def web_search(client, model: str, name: str, distance_km: float | None, today: date,
               year: int | None = None) -> Found | None:
    """Recherche via l'outil serveur web_search (aucun appel réseau fait par l'application elle-même)."""
    extra = f", format {distance_km:g} km" if distance_km else ""
    resp = client.messages.create(
        model=model, max_tokens=1200,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": SEARCH_PROMPT.format(
            today=today.isoformat(), name=name, extra=extra,
            edition=(f"l'édition {year}" if year else f"la prochaine édition (après le {today.isoformat()})"),
            dist=f"{distance_km:g} km" if distance_km else "le format principal")}])
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    js = json.loads(m.group(0))
    if not js.get("found") or not js.get("date"):
        return None
    d = date.fromisoformat(js["date"][:10])
    if d <= today or (year and d.year != year):                 # v0.25 : l'année demandée prime
        return None
    u = resp.usage
    n_search = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    tokens_eur = (u.input_tokens * 3 + u.output_tokens * 15) / 1e6 * 0.86
    url = js.get("url") or ""
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return Found(js.get("name") or name, d, js.get("distance_km"), js.get("dplus_m"), js.get("kind") or "trail",
                 host, round(tokens_eur + n_search * WEB_SEARCH_EUR, 4))


def _cross_check(f: Found, name: str, distance_km: float | None, today: date, year: int | None) -> Found:
    """Résultat web confronté au catalogue (v0.48) : si l'événement y est, le format du catalogue (même distance) fait
    foi pour le jour et l'heure de départ (la recherche web renvoie volontiers le 1er jour de l'événement)."""
    km = distance_km or f.distance_km or stated_distance(name)
    for r in catalog_event(f"{name} {f.name}", today, year):
        if km and _dist_ok(r, km):
            c = _found(r)
            if c.date != f.date:
                log.info("recherche web « %s » : %s corrigé en %s (catalogue)", name, f.date, c.date)
            c.cost_eur = f.cost_eur
            return c
    return f


def lookup(name: str, distance_km: float | None, today: date, llm=None, year: int | None = None) -> Found | None:
    """v0.48 : catalogue vérifié d'abord (0 €, jour du format exact), puis recherche web si le LLM est le vrai
    Claude (résultat confronté au catalogue), puis format à choisir si l'événement est connu. Jamais d'exception."""
    try:
        cat = from_catalog(name, distance_km, today, year)
    except Exception:                                            # noqa: BLE001
        log.exception("catalogue")
        cat = None
    if cat and not cat.ambiguous:
        return cat
    client = getattr(llm, "client", None)
    if client is not None and not cat:
        try:
            from .llm import MODELS
            f = web_search(client, MODELS["main"], name, distance_km, today, year)
            if f:
                return _cross_check(f, name, distance_km, today, year)
        except Exception as e:                                   # noqa: BLE001 (recherche web désactivée...)
            log.warning("recherche web de « %s » : %s", name, e)
    return cat
