"""v0.50 — « Mes zones » (Carrière › Réglages) : FC max, FC de repos, FC au seuil, zones et allures repères.

Mixin de CoachService. Les valeurs retenues (saisie > Strava > estimation > réglages, voir coach/hr_zones.py) sont
appliquées aux réglages du pilote : le moteur de charge des imports suivants et la détection du type de séance les
utilisent. Les cibles de séance (FC, allure, effort) en découlent : carte de séance, agenda Google, contexte du coach.
"""
from __future__ import annotations

import logging

from ..hr_zones import (
    LABELS,
    OVERRIDES_KEY,
    RESOLVED_KEY,
    STRAVA_KEY,
    TARGETS,
    HrZones,
    apply,
    estimate,
    resolve,
    target,
    validate,
)

log = logging.getLogger("coach.web")
PACE_LABELS = [("seuil", "Seuil"), ("10 km", "Allure 10 km"), ("vma", "VMA"), ("marathon", "Allure marathon"),
               ("endurance", "Endurance")]


class ZonesFlow:
    def _hr_base(self) -> tuple[float, float, float | None]:
        """Réglages du serveur d'origine (avant toute résolution), gardés sur l'objet de réglages."""
        st = self.pilot.settings
        if not hasattr(st, "_hr_base"):
            st._hr_base = (st.hr_max, st.hr_rest, st.lthr)
        return st._hr_base

    def hr_zones(self) -> HrZones:
        from ..pilot.store import PROFILES_KEY
        paces = (self.store.get("diagnosis") or {}).get("paces") or {}
        return resolve(self._hr_base(), self.store.get(OVERRIDES_KEY), self.store.get(STRAVA_KEY),
                       estimate(self.store.get(PROFILES_KEY) or {}, self.today()), paces)

    def apply_hr_settings(self) -> HrZones:
        """Résout les valeurs et les applique au moteur de charge (appelé au démarrage, après une synchro ou un
        import, et après une saisie)."""
        z = self.hr_zones()
        apply(self.pilot.settings, z)
        if self.store.get(RESOLVED_KEY) != z.as_dict():
            self.store.set(RESOLVED_KEY, z.as_dict())
        self._zones = z
        return z

    def session_target(self, s) -> dict | None:
        if s.is_race or s.intensity not in TARGETS:
            return None
        z = getattr(self, "_zones", None) or self.apply_hr_settings()
        return target(s.intensity, s.description, z)

    def calendar_extra(self, s) -> str:
        """Ligne ajoutée à l'événement Google Agenda d'une séance."""
        t = self.session_target(s)
        if not t:
            return ""
        return t["text"] + (f"\n{t['note']}" if t["note"] else "")

    # ---------------------------------------------------------------- Strava (GET /athlete/zones)
    def strava_zone_status(self) -> dict:
        api = self.pilot.strava
        connected = api is not None
        can = connected and hasattr(api, "zones")
        scope_ok = False
        if can:
            from ..pilot.strava_api import ZONES_SCOPE
            scope_ok = ZONES_SCOPE in api.scopes() if hasattr(api, "scopes") else True
        saved = self.store.get(STRAVA_KEY) or {}
        return {"connected": connected, "api": bool(can), "scope_ok": scope_ok,
                "needs_reconnect": bool(can and not scope_ok), "zones": saved.get("zones"), "at": saved.get("at"),
                "custom": saved.get("custom")}

    def refresh_strava_zones(self, force: bool = False) -> bool:
        """Zones FC de Strava, une fois par jour au plus (lecture d'API comptée dans le quota)."""
        st = self.strava_zone_status()
        if not (st["api"] and st["scope_ok"]):
            return False
        today = self.today().isoformat()
        if not force and (self.store.get(STRAVA_KEY) or {}).get("at") == today:
            return False
        try:
            js = self.pilot.strava.zones() or {}
        except Exception as e:                                   # noqa: BLE001 : quota, réseau : on réessaiera
            log.info("zones Strava : %s", e)
            return False
        hr = js.get("heart_rate") or {}
        zones = [{"min": int(z.get("min") or 0), "max": int(z.get("max") if z.get("max") is not None else -1)}
                 for z in hr.get("zones") or []]
        self.store.set(STRAVA_KEY, {"zones": zones or None, "custom": bool(hr.get("custom_zones")), "at": today})
        return True

    # ---------------------------------------------------------------- API
    def zones_view(self) -> dict:
        with self.lock:
            z = self.apply_hr_settings()
            base = self._hr_base()
            ov = self.store.get(OVERRIDES_KEY) or {}
            auto = resolve(base, {}, self.store.get(STRAVA_KEY), estimate(
                self.store.get("session_profiles") or {}, self.today()), z.paces)

            def val(k: str) -> dict:
                return {"value": round(getattr(z, k)), "source": LABELS[z.src[k]], "source_id": z.src[k],
                        "override": ov.get(k), "auto": round(getattr(auto, k)), "auto_source": LABELS[auto.src[k]]}
            targets = []
            for k, lbl in (("z1", "Récupération"), ("z2", "Footing"), ("long", "Sortie longue"), ("tempo", "Tempo"),
                           ("threshold", "Seuil"), ("vo2", "VMA (fractions longues)"), ("hills", "Côtes")):
                t = target(k, "", z)
                targets.append({"intensity": k, "label": lbl, **t})
            t = target("vo2", "10 × 30/30", z)
            targets.insert(6, {"intensity": "vo2_short", "label": "VMA courte (30/30)", **t})
            return {"hr_max": val("hr_max"), "hr_rest": val("hr_rest"), "lthr": val("lthr"), "zones": z.zones(),
                    "paces": [{"key": k, "label": lbl, "value": f"{z.paces[k]}/km"} for k, lbl in PACE_LABELS
                              if z.paces.get(k)],
                    "targets": targets, "strava": self.strava_zone_status(), "model": "Friel (% de la FC au seuil)"}

    def set_zones(self, values: dict) -> dict:
        """`values` : champs saisis (None = revenir à l'automatique). Lève ValueError si incohérent."""
        clean = validate(values)
        with self.lock:
            ov = {**(self.store.get(OVERRIDES_KEY) or {}), **clean}
            ov = {k: v for k, v in ov.items() if v}
            z = resolve(self._hr_base(), ov, self.store.get(STRAVA_KEY),
                        estimate(self.store.get("session_profiles") or {}, self.today()), {})
            mx, rest, lt = ov.get("hr_max") or z.hr_max, ov.get("hr_rest") or z.hr_rest, ov.get("lthr") or z.lthr
            if not rest < lt < mx:
                raise ValueError("il faut FC de repos < FC au seuil < FC max")
            self.store.set(OVERRIDES_KEY, ov)
            self.apply_hr_settings()
        return self.zones_view()


__all__ = ["ZonesFlow"]
