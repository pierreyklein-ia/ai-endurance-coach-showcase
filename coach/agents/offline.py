"""Équipe hors ligne (v0.40) : la même équipe, sans clé API, 100 % déterministe.

Utilisée par `HeuristicLLM` (mode hors ligne / repli quand Claude est indisponible) et par `MockLLM` pour les
nœuds non scriptés. Ce n'est PAS un raccourci : les outils sont réellement appelés (via `ToolBox.call`), les
constats viennent de leurs résultats, les fiches citées viennent de `knowledge_search`.

Contient aussi les contrôles déterministes du critique (`critic_checks`), utilisés EN PLUS du jugement du LLM
en mode Claude : un brouillon qui propose un rattrapage ou de l'intensité malgré un veto est toujours renvoyé.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from ..guardrails import HARD_INTENSITIES
from ..knowledge import tags_for
from ..rules_registry import R
from ..schemas import CoachDecision, Signals
from .roster import MAX_ROUTED, ORDER, TEAM
from .schemas import CriticVerdict, EyeReport, Priority, RoutingDecision, SpecialistNote
from .tools import ToolBox

NUTRI_TAGS = {"nutrition", "carbs", "gi", "gut", "hydration", "caffeine", "real_food"}
_GI = r"naus[eé]|vomi|ballonn|diarrh|[eé]c(oe|œ)ur|crampes? d.estomac|mal au ventre|estomac|digest"
_HEALTH_KW = r"bless|tendin|douleur|mal (au|à la|aux)|malade|fi[eè]vre|naus[eé]|vomi|malaise|vertige|insomni"
_CHARGE_KW = (r"motivation|me p[eè]sent|rajout|ajout|en faire plus|plus de volume|volume|charge|\bplan\b|semaine|s[ée]ance|d[ée]plac|fatigu|"
              r"r[ée]cup|\brpe\b|rat[ée]|manqu[ée]|rattrap|kilom|footing|fractionn|seuil|vma|jambes lourdes")
_COURSE_KW = (r"course|objectif|chrono|allure|\btemps\b|simul|aff[uû]t|taper|jour j|dossard|strat[ée]gie|diag|"
              r"ultra|marathon|ravito|barri[eè]re|d[ée]part|arriv[ée]e|vvx|sainté|utmb")
_MOTIV = r"motivation|motiv[ée]|m[ée] p[eè]sent?|lassitude|plus envie de courir|je me force|ras le bol de courir"
_NEG = r"(pas|jamais|sans|\bne\b|n'|aucun|inutile|interdit|non|plus de)\W+(\w+\W+){0,3}$"
_INTENSE_TXT = r"(seuil|vma|fractionn|intervalles?|tempo|c[ôo]tes|30 ?/ ?30|s[ée]ance (intense|dure|cl[ée]))"
_PROPOSE_INTENSE = (r"(fais|garde|maintiens|programme|on part sur|tu peux (faire|garder)|on garde|go pour|lance-toi sur)"
                    r"\W+(\w+\W+){0,4}?" + _INTENSE_TXT)
_CATCHUP_TXT = (r"(\b(on|je te propose de|tu peux|pour|va|vas|allez)\W+(\w+\W+){0,2}rattrap)|"
                r"(rattrap(e|er|ons|ez)\W+(ce|cette|demain|le week|samedi|dimanche|avec|les|tes|la|ta)\b)")
_UNSAFE = (r"malgr[ée] (la|ta|cette) douleur|serre les dents|ignore (la|ta) douleur|force (un peu )?quand m[êe]me|"
           r"anti-?inflammatoire|ibuprof|doliprane|parac[ée]tamol|cortico|cours avec (la|ta) fi[eè]vre")
LEAD = {"nutrition": "Côté nutrition :", "prevention": "Côté santé :", "charge": "Côté charge :",
        "course": "Côté course :"}
RISK_RANK = {"high": 0, "medium": 1, "low": 2}


def _positive(text: str, rx: str) -> bool:
    """Le motif apparaît au moins une fois SANS négation juste avant (« pas de rattrapage » ≠ « on rattrape »)."""
    t = text.lower()
    return any(not re.search(_NEG, t[max(0, m.start() - 40):m.start()]) for m in re.finditer(rx, t))


def worst_pain(sig: Signals) -> tuple[int, str | None]:
    if not sig.pain:
        return 0, None
    p = max(sig.pain, key=lambda x: x.severity_0_10)
    return p.severity_0_10, p.location


def health_signals(sig: Signals) -> list[str]:
    out = []
    sev, loc = worst_pain(sig)
    if sev:
        out.append(f"douleur {loc} {sev}/10")
    if sig.illness_symptoms:
        out.append("maladie (" + ", ".join(sig.illness_symptoms) + ")")
    if sig.alcohol in ("moderate", "heavy"):
        out.append("alcool" + (" important" if sig.alcohol == "heavy" else ""))
    if sig.sleep_hours is not None and sig.sleep_hours < R("recovery.min_sleep_hours"):
        out.append(f"{sig.sleep_hours:g} h de sommeil")
    elif (sig.sleep_quality_1_5 or 5) <= 2:
        out.append("mauvaise nuit")
    if (sig.fatigue_1_5 or 0) >= 4:
        out.append(f"fatigue {sig.fatigue_1_5}/5")
    if (sig.life_stress_1_5 or 0) >= 4 or (sig.emotional_distress_1_5 or 0) >= 3:
        out.append("stress de vie")
    return out


# --------------------------------------------------------------------------- superviseur
def route_rules(text: str, sig: Signals, days_to_race: int | None = None) -> RoutingDecision:
    t = text.lower()
    tags = tags_for(text)
    why: dict[str, str] = {}
    hs = health_signals(sig)
    if hs:
        why["prevention"] = "signal de santé : " + ", ".join(hs[:2])
    elif re.search(_HEALTH_KW, t) or tags & {"injury", "sleep", "stress", "hrv"}:
        why["prevention"] = "question de santé / récupération"
    if tags & NUTRI_TAGS or "[bilan nutrition" in t:
        why["nutrition"] = "nutrition à l'effort" + (" (troubles digestifs)" if re.search(_GI, t) else "")
        if re.search(_GI, t) and "prevention" not in why:
            why["prevention"] = "symptômes digestifs à l'effort"
    if sig.unavailable_dates or re.search(r"rattrap|rat[ée]\w* \d|manqu[ée]", t):
        why["charge"] = "séances manquées"
    elif sig.rpe_last_session or re.search(_CHARGE_KW, t):
        why["charge"] = "charge / plan de la semaine"
    if re.search(_COURSE_KW, t) and not re.search(r"^s[ée]ance du \d", t):
        why["course"] = "objectif / stratégie de course"
    elif days_to_race is not None and 0 <= days_to_race <= 15 and re.search(r"rajout|ajout|en faire plus|plus\b", t) \
            and "?" in t:
        why["course"] = f"affûtage (J-{days_to_race})"
    picked = [k for k in ORDER if k in why][:MAX_ROUTED]
    if not picked:
        return RoutingDecision(specialists=[], reason="aucun enjeu d'entraînement : le coach répond seul")
    return RoutingDecision(specialists=picked, reason=" ; ".join(f"{TEAM[k].label} : {why[k]}" for k in picked))


def shortcut(text: str, sig: Signals) -> RoutingDecision | None:
    """Cas évidents tranchés sans appel LLM (coût) ; la décision reste journalisée comme celle du superviseur."""
    t = text.lower().strip()
    if t.startswith("j'ai déplacé"):
        return RoutingDecision(specialists=["charge"], reason="règle : réaction à un déplacement de séance → Charge seule")
    if t.startswith("[bilan nutrition"):
        r = ["nutrition"] + (["prevention"] if re.search(_GI, t) or "mal passé" in t else [])
        return RoutingDecision(specialists=r, reason="règle : bilan nutrition d'une sortie longue")
    if re.match(r"^s[ée]ance du \d", t) and not health_signals(sig) and "?" not in t:
        return RoutingDecision(specialists=["charge"], reason="règle : retour de séance → Charge")
    return None


# --------------------------------------------------------------------------- spécialistes
def _cards(box: ToolBox, tags: set[str], query: str = "", k: int = 3) -> list[str]:
    """Fiches pour les thèmes du spécialiste (la question n'ajoute pas de thème : pas de fiche hors sujet)."""
    out = box.call("knowledge_search", {"tags": sorted(tags)[:8], "query": "", "k": k})
    return [c["id"] for c in out if isinstance(c, dict) and "id" in c][:k] if isinstance(out, list) else []


def note_charge(box: ToolBox, p: dict, sig: Signals) -> SpecialistNote:
    msg, eye = p.get("message", "").lower(), p.get("mode") == "eye"
    st, plan = box.call("load_state"), box.call("plan_7d", {"days": 14 if eye else 7})
    rec = box.call("recent_activities", {"days": 56 if eye else 14})
    f, r, tags = [], [], {"load", "acwr"}
    acwr = st.get("acwr")
    f.append(f"ACWR {acwr if acwr is not None else 'n/d'} ({st.get('zone_acwr')}), fraîcheur (TSB) {st.get('tsb')}")
    f.append(f"{plan.get('hard_sessions', 0)} séance(s) dure(s) prévue(s) sur 7 jours, charge prévue {plan.get('planned_load')}")
    f.append(f"{rec.get('count', 0)} sortie(s) sur {56 if eye else 14} jours ({round(rec.get('minutes', 0) / 60, 1)} h)")
    zone = st.get("zone_acwr", "")
    risk = "high" if zone in ("danger", "élevé") else "medium" if zone == "vigilance" else "low"
    k: list[str] = []
    if zone in ("danger", "élevé", "vigilance"):
        r.append("Stabiliser la charge cette semaine : pas de hausse de volume ni d'intensité ajoutée")
        k.append(f"Surcharge : ACWR {acwr} en zone {zone}")
    if sig.unavailable_dates or re.search(r"rattrap|manqu|rat[ée]", msg):
        tags |= {"missed_sessions", "detraining"}
        r.insert(0, "Pas de rattrapage : on reprend le plan là où il en est, les séances manquées sont perdues sans dommage")
        k.append("Tentation de rattraper : pic de charge sur peu de jours")
        risk = "medium" if risk == "low" else risk
    if re.search(r"rajout|ajout|en faire plus|une s[ée]ance de plus", msg):
        sessions = plan.get("sessions") or []
        day = sessions[1]["day"] if len(sessions) > 1 else p.get("today")
        dry = box.call("guardrails_dry_run", {"changes": [{"action": "add", "session_id": "essai-ajout", "day": day,
                                                           "intensity": "vo2", "duration_min": 60, "planned_load": 90}]})
        if isinstance(dry, dict) and not dry.get("allowed", True):
            f.append("Ajout d'une séance intense testé : refusé par les garde-fous ("
                     + ", ".join(v["rule"] for v in dry.get("violations", []) if v["severity"] == "block") + ")")
            k.append("Une séance intense de plus dépasserait les garde-fous")
        r.append("Ne rien ajouter au plan : la charge prévue suffit")
        j = plan.get("days_to_race")
        if j is not None and 0 <= j <= 15:
            tags.add("taper")
    if re.search(_MOTIV, msg):
        r.insert(0, "Garder les 2 séances clés, rendre les autres libres : footing plaisir, en groupe ou sans montre")
        k.append("Lassitude : risque de décrocher du plan si rien ne change")
        tags.add("recovery")
        risk = "medium" if risk == "low" else risk
    if (sig.fatigue_1_5 or 0) >= 4 or (sig.rpe_last_session or 0) >= 8:
        tags.add("recovery")
        r.append("Alléger la prochaine séance dure (footing facile à la place)")
        k.append("Fatigue élevée avant une séance dure")
        risk = "medium" if risk == "low" else risk
    if eye:
        adh = box.call("plan_adherence", {"weeks": 8})
        wl = box.call("weekly_loads", {"weeks": 8})
        if isinstance(adh, list) and adh:
            pl, dn = sum(w["planned"] for w in adh), sum(w["done"] for w in adh)
            f.append(f"Assiduité sur {len(adh)} semaine{'s' if len(adh) > 1 else ''} : {dn}/{pl} séances prévues "
                     f"faites ({round(100 * dn / pl) if pl else 0} %)")
            if pl and dn / pl < 0.7:
                r.append("Revoir le plan pour qu'il tienne dans ta vie : moins de séances, mieux placées")
                k.append("Assiduité sous 70 % : le plan ne tient pas dans l'emploi du temps")
                risk = "medium" if risk == "low" else risk
        if isinstance(wl, list) and len(wl) >= 3:
            loads = [w["load"] for w in wl]
            f.append(f"Charge hebdo : {loads[0]} → {loads[-1]} (min {min(loads)}, max {max(loads)})")
            if max(loads) > 1.5 * max(1, sorted(loads)[len(loads) // 2]):
                r.append("Lisser les semaines : éviter les pics de charge isolés")
                tags.add("volume")
        tags |= {"planning", "intensity_distribution"}
        r.append("Garder deux séances de qualité par semaine au plus, tout le reste en endurance facile")
    if not r:
        r.append("Garder le plan tel quel : charge dans la zone prévue")
    return SpecialistNote(findings=f, recommendations=r[:3], risks=k[:5], risk_level=risk,
                          evidence_ids=_cards(box, tags, msg), confidence=0.8 if acwr is not None else 0.5)


def note_nutrition(box: ToolBox, p: dict, sig: Signals) -> SpecialistNote:
    msg, eye = p.get("message", "").lower(), p.get("mode") == "eye"
    logs, pal = box.call("nutrition_logs"), box.call("food_palette")
    tgt = box.call("target_carbs", {"duration_h": 3.0})
    f, r, tags = [], [], {"nutrition", "carbs"}
    n, tm = logs.get("count", 0), logs.get("trained_max_g_h")
    f.append(f"{n} bilan(s) nutrition enregistré(s)" + (f", maximum toléré {tm:.0f} g/h" if tm else ""))
    last = (logs.get("last") or [None])[0]
    if last:
        f.append(f"Dernier bilan : {last['carbs_g_h']:.0f} g/h, digestion {last['digestion']}/5"
                 + (" (" + ", ".join(last["symptoms"]) + ")" if last["symptoms"] else ""))
    gi = bool(re.search(_GI, msg)) or "mal passé" in msg or bool(last and last["digestion"] >= 4)
    lo, hi = (tgt.get("range_g_h") or [60, 90])
    r.append(f"Viser {lo} à {hi} g/h de glucides sur les sorties de plus de 2 h 30, jamais plus de 120 g/h")
    risk, k = "low", []
    if gi:
        k.append("Troubles digestifs à l'effort : risque de ne plus pouvoir s'alimenter en course")
        tags |= {"gi", "gut"}
        risk = "medium"
        r.insert(0, "Fractionner : une petite prise toutes les 20 minutes, boisson moins concentrée, rien de gras ni de fibreux")
        r.append("Ne changer qu'une seule chose à la prochaine sortie longue pour savoir ce qui ne passe pas")
    elif tm:
        tags.add("gut")
        r.append("Entraîner l'estomac : +10 g/h au plus par sortie longue")
    if pal.get("avoid"):
        f.append("À éviter d'après tes bilans : " + ", ".join(pal["avoid"][:3]))
    if pal.get("tolerated"):
        f.append("Passe bien : " + ", ".join(pal["tolerated"][:3]))
    if re.search(r"chaleur|chaud|canicule", msg):
        tags |= {"heat", "hydration"}
        r.append("Chaleur : boire régulièrement et ajouter du sodium, sans forcer sur l'eau")
        k.append("Chaleur : déshydratation, ou hyponatrémie si l'on boit trop d'eau sans sel")
    tags |= tags_for(msg) & (NUTRI_TAGS | {"heat"})
    if eye:
        race = box.call("race_nutrition_plan")
        if isinstance(race, dict) and race.get("target_g_h"):
            f.append(f"Plan de course : {race['target_g_h']} g/h, {race.get('fluid_ml_h')} ml/h d'eau")
            if race.get("rehearsal_needed"):
                r.append("Répéter le plan de course à l'entraînement avant le jour J")
                k.append("Plan de course pas encore répété à l'entraînement")
        if n < 3:
            r.append("Faire un bilan nutrition après chaque sortie de plus d'1 h 30")
    return SpecialistNote(findings=f, recommendations=r[:3], risks=k[:5], risk_level=risk,
                          evidence_ids=_cards(box, tags, msg), confidence=0.8 if n else 0.5)


def note_course(box: ToolBox, p: dict, sig: Signals) -> SpecialistNote:
    msg, eye = p.get("message", "").lower(), p.get("mode") == "eye"
    goal, ph = box.call("main_goal"), box.call("specific_phase")
    est = box.call("progress_estimates")
    f, r, tags, sim = [], [], {"race"}, None
    j, phase = ph.get("days_to_race"), ph.get("phase", "inconnue")
    if goal.get("name"):
        f.append(f"{goal['name']}" + (f" dans {j} jours" if j is not None else "") + f" (phase {phase})")
    if isinstance(est, dict) and est.get("goal_estimate_s"):
        lo, mid, hi = est["goal_estimate_s"]
        f.append(f"Temps estimé de l'objectif : {mid // 3600} h {mid % 3600 // 60:02d} (entre {lo // 3600} h et {hi // 3600 + 1} h)")
    if eye or re.search(r"chrono|temps|simul|allure|strat[ée]gie|barri[eè]re", msg):
        sim = box.call("race_simulation")
        if isinstance(sim, dict) and sim.get("headline"):
            f.append(sim["headline"])
            cl = (sim.get("climbs") or [None])[0]
            if cl:
                f.append(f"Plus grosse montée : {cl['name']} (km {cl['km0']:g} à {cl['km1']:g}, +{cl['gain_m']} m)")
            cut = (sim.get("cutoffs") or [None])[0]
            if cut and cut.get("margin_min") is not None:
                f.append(f"Barrière la plus serrée : {cut['name']} ({cut['clock']}), {cut['margin_min']} min de marge "
                         "en scénario prudent")
            r.append("Partir sur l'allure du scénario prudent la première moitié, accélérer ensuite si tout va bien")
            tags.add("durability")
    if phase == "affûtage":
        tags.add("taper")
        r.insert(0, "Affûtage : volume en baisse, intensité gardée, rien à ajouter")
    elif phase == "spécifique":
        tags |= {"trail", "durability"}
        r.append("Relier chaque séance clé au profil de la course : côtes, descentes, allure, nutrition du jour J")
    elif phase == "foncière":
        tags |= {"intensity_distribution", "volume"}
        r.append("Construire la base : beaucoup d'endurance facile, deux séances de qualité par semaine au plus")
    elif phase == "après course":
        tags |= {"recovery_ultra"}
        r.append("Récupération après course : reprise très progressive")
    if ph.get("brief"):
        f.append(ph["brief"][:200])
    if not r:
        r.append("Garder le cap sur l'objectif principal")
    risk = "medium" if phase == "affûtage" and re.search(r"rajout|ajout|en faire plus", msg) else "low"
    k = ["Ajouter de la charge pendant l'affûtage : arriver fatigué au départ"] if risk == "medium" else []
    tight = [c for c in (sim.get("cutoffs") or []) if isinstance(c, dict) and c.get("margin_min") is not None
             and c["margin_min"] < 60] if isinstance(sim, dict) else []
    if tight:
        k.append(f"Barrière horaire serrée : {tight[0]['name']} ({tight[0]['margin_min']:.0f} min de marge, scénario prudent)")
    return SpecialistNote(findings=f, recommendations=r[:3], risks=k, risk_level=risk,
                          evidence_ids=_cards(box, tags, msg), confidence=0.7 if goal.get("name") else 0.4)


def note_prevention(box: ToolBox, p: dict, sig: Signals) -> SpecialistNote:
    msg, eye = p.get("message", "").lower(), p.get("mode") == "eye"
    w = box.call("wellbeing_signals")
    if isinstance(w, dict) and "pain" in w:              # les constats viennent de l'outil, pas du message brut
        sig = Signals.model_validate({**{k: v for k, v in w.items() if k in Signals.model_fields and v is not None},
                                      "illness_symptoms": w.get("illness") or []})
    fb = box.call("recent_feedback", {"days": 56 if eye else 14})
    f, r, tags, veto = [], [], {"recovery"}, []
    thr = (w.get("seuils") or {}).get("douleur_max_intensite", R("pain.max_for_intensity")) if isinstance(w, dict) \
        else R("pain.max_for_intensity")
    sev, loc = worst_pain(sig)
    if sev:
        tags |= {"injury", "pain"}
        f.append(f"Douleur {loc} {sev}/10 (seuil d'intensité du registre : {thr}/10)")
        if sev > thr:
            veto.append(f"douleur {loc} {sev}/10 > {thr}")
            r.append("Pas d'intensité : footing très facile ou repos ; consulter si la douleur persiste 48 h ou augmente")
        else:
            r.append("Surveiller la douleur : arrêter la séance si elle augmente")
    if sig.illness_symptoms:
        tags.add("illness")
        veto.append("symptômes de maladie")
        f.append("Symptômes : " + ", ".join(sig.illness_symptoms))
        r.append("Repos tant qu'il y a de la fièvre ; reprise progressive, en facile, après 24 h sans symptôme")
    if sig.alcohol == "heavy":
        veto.append("alcool important (récupération compromise)")
        f.append("Alcool important déclaré")
        r.append("Pas d'intensité dans les 36 h ; hydratation, repos ou footing très facile")
    short = sig.sleep_hours is not None and sig.sleep_hours < R("recovery.min_sleep_hours")
    if short or (sig.sleep_quality_1_5 or 5) <= 2:
        tags.add("sleep")
        veto.append("nuit très courte")
        f.append(f"Sommeil : {sig.sleep_hours:g} h" if sig.sleep_hours is not None else "Sommeil de mauvaise qualité")
        r.append("Priorité au sommeil : rien d'intense avant une bonne nuit")
    if (sig.fatigue_1_5 or 0) >= 5:
        veto.append("fatigue extrême")
    if (sig.fatigue_1_5 or 0) >= 4:
        f.append(f"Fatigue {sig.fatigue_1_5}/5")
    if (sig.life_stress_1_5 or 0) >= 4 or (sig.emotional_distress_1_5 or 0) >= 3:
        tags.add("stress")
        f.append("Stress de vie élevé")
        r.append("Alléger l'intensité du jour, sans l'imposer ; un footing relâché aide à décompresser")
    if re.search(_GI, msg):
        tags.add("gi")
        f.append("Symptômes digestifs à l'effort")
        r.append("Si les nausées reviennent à chaque sortie, ou avec malaise ou vertiges, en parler à un médecin")
    if isinstance(fb, dict) and fb.get("rpe_avg"):
        f.append(f"RPE moyen récent {fb['rpe_avg']}/10 sur {fb['count']} retour(s)")
        if fb["rpe_avg"] >= 8:
            r.append("Séances ressenties très dures : vérifier la récupération entre deux séances clés")
    if eye or (sig.fatigue_1_5 or 0) >= 4:
        d = box.call("hr_drift")
        vals = [v["decoupling_pct"] for v in (d.get("values") or []) if v.get("decoupling_pct") is not None] \
            if isinstance(d, dict) else []
        if vals:
            f.append(f"Dérive cardiaque des sorties longues : {vals[-1]} % (dernière), {min(vals)} à {max(vals)} %")
            if vals[-1] > 8:
                tags.add("durability")
                r.append("Dérive cardiaque élevée : plus d'endurance facile et une nutrition régulière sur les longues")
    if eye:
        tags |= {"strength", "injury", "sleep"}
        r.append("Deux courtes séances de renforcement par semaine (quadriceps, mollets, ischios) pour encaisser les descentes")
        r.append("Protéger le sommeil (7 h et plus) les veilles et lendemains de séance clé")
    if not f:
        f.append("Aucun signal de douleur, de maladie ni de fatigue anormale" + (" sur la période" if eye else " dans le message"))
    risk = "high" if veto else "medium" if len(f) > 1 or (sig.fatigue_1_5 or 0) >= 4 else "low"
    if not r:
        r.append("Rien à signaler côté santé : continuer à écouter tes sensations")
    cards = _cards(box, tags - ({"strength"} if eye else set()), msg)
    if eye:                                              # 2e recherche : renforcement (prévention en descente)
        cards += [c for c in _cards(box, {"strength", "injury", "trail"}, k=2) if c not in cards]
    k = [x[0].upper() + x[1:] for x in veto] + (["Stress de vie élevé : récupération moins bonne"]
                                                 if "Stress de vie élevé" in f else [])
    return SpecialistNote(findings=f[:6], recommendations=r[:3], risks=k[:5], risk_level=risk, evidence_ids=cards,
                          confidence=0.85 if sig.pain or sig.illness_symptoms else 0.7,
                          veto=bool(veto), veto_reason=", ".join(veto) if veto else None)


NOTES = {"charge": note_charge, "nutrition": note_nutrition, "course": note_course, "prevention": note_prevention}


def specialist(key: str, box: ToolBox, payload: dict) -> SpecialistNote:
    sig = Signals.model_validate(payload.get("signals") or {})
    note = NOTES[key](box, payload, sig)
    note.evidence_ids = [c for c in note.evidence_ids if c in box.seen_cards]
    return note


# --------------------------------------------------------------------------- critique (contrôles déterministes)
def _carbs_over(text: str, cap: int = 120) -> int | None:
    for m in re.finditer(r"(\d{2,3})\s*(?:à\s*\d{2,3}\s*)?(?:g|grammes?)\s*(?:/|par)\s*h", text.lower()):
        if int(m.group(1)) > cap:
            return int(m.group(1))
    for m in re.finditer(r"à\s*(\d{2,3})\s*(?:g|grammes?)\s*(?:/|par)\s*h", text.lower()):
        if int(m.group(1)) > cap:
            return int(m.group(1))
    return None


def critic_checks(draft: CoachDecision, notes: list[dict], known_cards: set[str], signals: Signals,
                  plan_ids: set[str] | None = None) -> list[str]:
    """Raisons BLOQUANTES (→ révision). Le critique LLM peut en ajouter ; il ne peut pas en retirer."""
    out: list[str] = []
    text = draft.message_to_athlete
    veto = next((n for n in notes if n.get("veto")), None)
    proposed = [c for c in draft.changes if c.action in ("add", "modify")]
    hard = [c for c in proposed if c.session.intensity in HARD_INTENSITIES and not c.session.is_race]
    if veto and (hard or _positive(text, _PROPOSE_INTENSE)):
        what = hard[0].session.intensity if hard else "séance intense"
        out.append(f"Veto de Prévention ({veto.get('veto_reason') or 'santé'}) non respecté : le brouillon propose {what}.")
    added_hard = [c for c in draft.changes if c.action == "add" and c.session.intensity in HARD_INTENSITIES]
    if any("rattrap" in (c.reason + " " + c.session.description).lower() for c in draft.changes) \
            or _positive(text, _CATCHUP_TXT) or (signals.unavailable_dates and added_hard):
        out.append("Rattrapage proposé après des séances manquées : interdit (perdu, pas à rattraper).")
    nutri = next((n for n in notes if n.get("name") == "nutrition"), None)
    if nutri and re.search(r"gras|fibr", " ".join(nutri.get("recommendations") or [])) \
            and _positive(text, r"\b(plus|davantage|mange|prends)\W+(\w+\W+){0,2}(gras|fibres?|fibreux)"):
        out.append("Contradiction avec Nutrition (troubles digestifs : rien de gras ni de fibreux pendant l'effort).")
    over = _carbs_over(text)
    if over:
        out.append(f"{over} g/h de glucides : au-delà du plafond de 120 g/h.")
    m = re.search(_UNSAFE, text.lower())
    if m:
        out.append(f"Conseil dangereux ou médical (« {m.group(0)} ») : à retirer, orienter vers un professionnel.")
    unknown = [e for e in draft.evidence_ids if known_cards and e not in known_cards]
    if unknown:
        out.append("Fiche citée inconnue (" + ", ".join(unknown[:3]) + ") : ne citer que les fiches fournies.")
    charge = next((n for n in notes if n.get("name") == "charge"), None)
    if charge and charge.get("risk_level") == "high" and added_hard:
        out.append("Charge à risque élevé (ACWR) : l'ajout d'une séance intense n'est pas justifié.")
    if plan_ids is not None:
        ghost = [c.session.session_id for c in draft.changes if c.action in ("modify", "cancel", "move")
                 and c.session.session_id not in plan_ids]
        if ghost:
            out.append("Changement sur une séance absente du plan (" + ghost[0] + ") : réutiliser le session_id existant.")
    return out


def critic_remarks(draft: CoachDecision, notes: list[dict]) -> list[str]:
    """Remarques NON bloquantes (trace, évaluation) : fondement, couverture, respect du personnage."""
    out = []
    if re.search(r"\bvous\b|\bvotre\b|\bvos\b", draft.message_to_athlete.lower()):
        out.append("Personnage : le coach tutoie l'athlète (pas de vouvoiement).")
    if len(re.findall(r"[.!?](\s|$)", draft.message_to_athlete)) > 6:
        out.append("Personnage : message trop long (4 phrases visées).")
    if any(n.get("evidence_ids") for n in notes) and not draft.evidence_ids:
        out.append("Aucune fiche citée alors que l'équipe en fournit.")
    for n in notes:
        if n.get("risk_level") == "high" and not draft.changes and n.get("name") == "prevention" \
                and not re.search(r"douleur|repos|facile|sommeil|malad|consult|r[ée]cup|nuit|fatigu", draft.message_to_athlete.lower()):
            out.append("Le risque santé signalé par Prévention n'est pas évoqué dans le message.")
    return out


def offline_critic(payload: dict) -> CriticVerdict:
    draft = CoachDecision.model_validate(payload["draft"])
    sig = Signals.model_validate(payload.get("signals") or {})
    reasons = critic_checks(draft, payload.get("team") or [], set(payload.get("known_cards") or []), sig,
                            set(payload["plan_ids"]) if payload.get("plan_ids") is not None else None)
    return CriticVerdict(verdict="revise" if reasons else "ok", reasons=reasons or critic_remarks(draft, payload.get("team") or []))


# --------------------------------------------------------------------------- coach (synthèse hors ligne)
def team_synthesis(decision: CoachDecision, payload: dict) -> CoachDecision:
    """Le coach hors ligne tient compte de l'équipe : fiches citées, recommandation prioritaire, veto."""
    team = payload.get("team") or []
    if not team:
        return decision
    valid = {c["id"] for c in payload.get("team_cards") or []}
    cited = list(dict.fromkeys(decision.evidence_ids + [c for n in team for c in n.get("evidence_ids", []) if c in valid]))[:4]
    changes = decision.changes
    if any(n.get("veto") for n in team):
        changes = [c for c in changes if not (c.action in ("add", "modify") and c.session.intensity in HARD_INTENSITIES)]
    msg = decision.message_to_athlete
    if not payload.get("athlete_message", "").lower().startswith("j'ai déplacé"):
        ranked = sorted(team, key=lambda n: (RISK_RANK.get(n.get("risk_level"), 2), ORDER.index(n["name"])))
        filler = re.compile(r"^(rien à signaler|garder le plan tel quel|garder le cap)", re.I)
        for n in ranked:
            rec = next((x for x in n.get("recommendations", []) if x[:20].lower() not in msg.lower()
                        and not filler.match(x)), None)
            if rec:
                msg = msg.rstrip() + f" {LEAD[n['name']]} {rec[0].lower() + rec[1:]}."
                break
    return decision.model_copy(update={"evidence_ids": cited, "changes": changes, "message_to_athlete": msg})


# --------------------------------------------------------------------------- Œil du coach (rapport hors ligne)
def eye_report(payload: dict) -> EyeReport:
    team = {n["name"]: n for n in payload.get("team") or []}
    today = date.fromisoformat(payload["today"])
    ch, nu, co, pr = (team.get(k, {}) for k in ("charge", "nutrition", "course", "prevention"))
    first = lambda n, i=0: (n.get("findings") or [""])[i] if len(n.get("findings") or []) > i else ""   # noqa: E731
    sent = lambda x: (x[0].upper() + x[1:]).rstrip(".") + "." if x else ""                              # noqa: E731
    summary = " ".join(x for x in (
        "J'ai relu tes 8 dernières semaines avec toute l'équipe.", sent(first(co)), sent(first(ch)),
        sent(first(ch, 3)) if "Assiduité" in first(ch, 3) else "",
        "Voici ce qui marche, ce qui coince et tes 3 priorités pour le mois qui vient.") if x)
    strengths, weaknesses = [], []
    for n in (ch, nu, co):
        for f in n.get("findings") or []:
            pct = re.search(r"\((\d{1,3}) %\)", f)
            if pct and int(pct.group(1)) >= 80:
                strengths.append(f)
            elif pct and int(pct.group(1)) < 70:
                weaknesses.append(f)
    if nu.get("findings") and "Passe bien" in " ".join(nu["findings"]):
        strengths.append(next(f for f in nu["findings"] if f.startswith("Passe bien")))
    if ch.get("risk_level") == "low":
        strengths.append("Charge maîtrisée : " + first(ch))
    if not strengths:
        strengths.append("Tu donnes des retours au coach : c'est ce qui permet d'ajuster le plan")
    for n in (ch, nu, pr):
        if n.get("risk_level") in ("medium", "high"):
            weaknesses.extend((n.get("recommendations") or [])[:1])
    if not weaknesses:
        weaknesses.append("Pas de point faible marqué sur la période : la régularité reste la clé")
    risks = [f"{TEAM[k].label} : {team[k].get('veto_reason') or team[k]['findings'][0]}" for k in ORDER
             if k in team and team[k].get("risk_level") == "high" and team[k].get("findings")]
    if not risks:
        risks.append("Aucun risque élevé détecté par l'équipe")
    recs = []
    ranked = sorted(team.values(), key=lambda n: (RISK_RANK.get(n.get("risk_level"), 2), ORDER.index(n["name"])))
    filler = re.compile(r"^(rien à signaler|garder le plan tel quel|garder le cap)", re.I)
    for n in ranked:
        useful = [x for x in n.get("recommendations") or [] if not filler.match(x)]
        for i, rec in enumerate(useful):
            recs.append((i, RISK_RANK.get(n.get("risk_level"), 2), ORDER.index(n["name"]), rec, n))
    recs.sort(key=lambda x: (x[0], x[1], x[2]))
    prios, used = [], set()
    for _, _, _, rec, n in recs:
        if n["name"] in used and len(team) >= 3:
            continue
        used.add(n["name"])
        prios.append(Priority(action=rec, why=f"{TEAM[n['name']].label} : " + sent((n.get("findings") or [""])[0]),
                              cards=_cards_for(rec, n.get("evidence_ids") or [])))
        if len(prios) == 3:
            break
    measure = []
    if nu:
        measure.append("Glucides tolérés (g/h) et digestion à chaque sortie de plus d'1 h 30 (bilan nutrition)")
    if pr:
        measure.append("Dérive cardiaque des sorties longues et RPE des séances clés")
    if co:
        measure.append("Temps estimé de l'objectif dans le simulateur, après chaque sortie longue en côte")
    if ch:
        measure.append("Assiduité : séances prévues faites chaque semaine")
    return EyeReport(summary=summary[:900], strengths=strengths[:4], weaknesses=weaknesses[:4], risks=risks[:4],
                     to_measure=measure[:3],
                     nutrition=" ".join(sent(x) for x in (nu.get("findings") or [])[:2] + (nu.get("recommendations") or [])[:2])[:700],
                     race=" ".join(sent(x) for x in (co.get("findings") or [])[:2] + (co.get("recommendations") or [])[:2])[:700],
                     priorities=prios, next_review=f"Dans 30 jours, le {(today + timedelta(days=30)):%d/%m}, "
                                                   "ou plus tôt si une douleur apparaît.")


def _cards_for(text: str, cited: list[str]) -> list[str]:
    """Fiches de la note qui parlent du thème de la recommandation (sinon les premières de la note)."""
    from ..knowledge import KNOWLEDGE
    tg = {e.id: set(e.tags) for e in KNOWLEDGE}
    want = tags_for(text) | ({"intensity_distribution"} if re.search(r"qualit|facile", text.lower()) else set())
    hit = [c for c in cited if tg.get(c, set()) & want]
    return (hit or cited)[:2]


def eye_checks(rep: EyeReport, notes: list[dict], known_cards: set[str]) -> list[str]:
    out = []
    if not rep.to_measure:
        out.append("Dire quoi mesurer d'ici le prochain point (`to_measure`).")
    if len(rep.priorities) != 3:
        out.append(f"Il faut exactement 3 priorités (il y en a {len(rep.priorities)}).")
    for p in rep.priorities:
        if not p.cards:
            out.append(f"Priorité sans fiche citée : « {p.action[:60]} ».")
        elif known_cards and any(c not in known_cards for c in p.cards):
            out.append(f"Fiche inconnue citée pour « {p.action[:60]} ».")
    blob = " ".join([rep.summary, rep.nutrition, rep.race] + [p.action for p in rep.priorities])
    if _carbs_over(blob):
        out.append("Plus de 120 g/h de glucides dans le rapport.")
    if _positive(blob, _CATCHUP_TXT):
        out.append("Rattrapage proposé dans le rapport.")
    if any(n.get("veto") for n in notes) and any(_positive(p.action, _PROPOSE_INTENSE) for p in rep.priorities):
        out.append("Veto de Prévention : pas de priorité d'intensité.")
    return out


def offline_structured(node: str, user: str, schema):
    """Nœuds de l'équipe appelés en sortie structurée (sans outils)."""
    p = json.loads(user)
    if node == "supervisor":
        out = route_rules(p.get("message", ""), Signals.model_validate(p.get("signals") or {}), p.get("days_to_race"))
    elif node == "critic":
        out = offline_critic(p)
    elif node == "eye_critic":
        rep = EyeReport.model_validate(p["draft"])
        reasons = eye_checks(rep, p.get("team") or [], set(p.get("known_cards") or []))
        out = CriticVerdict(verdict="revise" if reasons else "ok", reasons=reasons)
    elif node == "eye_report":
        out = eye_report(p)
    else:
        raise KeyError(node)
    return schema.model_validate(out.model_dump())


def offline_agent(node: str, user: str, tools: list, schema):
    """Spécialiste hors ligne : `node` = spec_<clé> ; les outils de `tools` sont réellement exécutés."""
    from ..llm import AgentResult
    box = tools if isinstance(tools, ToolBox) else ToolBox(list(tools))
    key = node.removeprefix("eye_").removeprefix("spec_")
    note = specialist(key, box, json.loads(user))
    return AgentResult(schema.model_validate(note.model_dump()), list(box.calls), steps=1)


TEAM_NODES = {"supervisor", "critic", "eye_report", "eye_critic"}

__all__ = ["route_rules", "shortcut", "specialist", "critic_checks", "critic_remarks", "offline_critic",
           "team_synthesis", "eye_report", "eye_checks", "offline_structured", "offline_agent", "health_signals",
           "TEAM_NODES"]
