"""v0.38 — Plan nutrition de course, ravito par ravito, construit sur ce que l'athlète a testé à l'entraînement.

Entrées : les tronçons entre deux ravitaillements (durées du scénario cible du simulateur), les bilans
nutrition des sorties longues (`coach.nutrition.summary` : plus haut apport toléré, aliments qui passent,
aliments à éviter), la chaleur, la liste des ravitos de l'organisateur.

Règles (fiches nutrition de `knowledge.py`, Jeukendrup 2014 et 2017, Tiller 2019, Costa 2017,
Hew-Butler 2015) :
- glucides/h = `target_carbs_g_h(durée, max toléré)` ; on vise le max toléré (borné par la fourchette) ;
  SANS bilan : 60 g/h (plafond 70) et une répétition sur les sorties longues est demandée ;
  jamais plus de 120 g/h, y compris au ravito ;
- produits : d'abord ceux qui passent bien chez l'athlète, jamais ceux à éviter ;
- caféine seulement dans le dernier tiers de la course ;
- eau et sodium selon la chaleur (boire selon la soif, pas plus de ~800 ml/h) ;
- matériel obligatoire : réserve d'eau (0,5 L min), un produit énergétique, gobelet.
"""
from __future__ import annotations

import math

from .nutrition import summary
from .nutrition_foods import BY_ID, target_carbs_g_h

MAX_G_H = 120
NO_LOG_AIM, NO_LOG_CAP = 60, 70
FLASK_ML = 500
FLUID_ML_H = {"fraiche": 400, "normale": 550, "chaude": 750}
SODIUM_MG_H = {"fraiche": 300, "normale": 450, "chaude": 700}
CAFFEINE_MAX_MG = 200
PORTABLE = ["gel", "gel_hd", "gommes", "compote_sport", "pate_fruits", "barre", "compote", "miel", "bonbons",
            "pain_epices", "barre_cereales", "puree", "banane"]
DEFAULT_CARRY = ["gel", "compote_sport", "pate_fruits", "barre"]
# boisson sucrée du ravito : composition inconnue, valeurs prudentes
AID_DRINK = {"id": "boisson_sucree", "name": "Boisson sucrée du ravito", "carbs_g": 15, "sodium_mg": 50,
             "fluid_ml": 250, "caffeine_mg": 0}
AID_MAP = {"pate_fruits": "pate_fruits", "pain_epices": "pain_epices", "biscuits_sales": "biscuits_sales",
           "quatre_quarts": "quatre_quarts", "fruits_secs": "fruits_secs", "fromage": "fromage",
           "charcuterie": "charcuterie", "soupe": "soupe", "cola": "cola"}


def _item(fid: str, qty: float = 1) -> dict:
    if fid == "boisson_sucree":
        f = AID_DRINK
        return {"id": fid, "name": f["name"], "qty": qty, "carbs_g": f["carbs_g"] * qty, "sodium_mg": f["sodium_mg"] * qty,
                "fluid_ml": f["fluid_ml"] * qty, "caffeine_mg": 0}
    f = BY_ID[fid]
    return {"id": fid, "name": f.name, "qty": qty, "portion": f.portion, "carbs_g": f.carbs_g * qty,
            "sodium_mg": f.sodium_mg * qty, "fluid_ml": f.fluid_ml * qty, "caffeine_mg": f.caffeine_mg * qty}


def _sum(items: list[dict], k: str) -> float:
    return float(sum(i[k] for i in items))


def _merge(items: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for it in items:
        if it["id"] in out:
            o = out[it["id"]]
            for k in ("qty", "carbs_g", "sodium_mg", "fluid_ml", "caffeine_mg"):
                o[k] += it[k]
        else:
            out[it["id"]] = dict(it)
    return list(out.values())


def _label(items: list[dict]) -> str:
    def one(i):
        q = i["qty"]
        qs = str(int(q)) if q == int(q) else str(q).replace(".", ",")
        return f"{qs} × {i['name'].lower()}"
    return ", ".join(one(i) for i in items)


def aid_offer(meta: dict) -> tuple[set[str], set[str]]:
    """(toujours proposé, parfois proposé) chez l'organisateur, en identifiants de la palette."""
    af = meta.get("aid_food") or {}
    always = {AID_MAP[x] for x in af.get("solid", []) if x in AID_MAP}
    if "boisson_sucree" in af.get("liquid", []):
        always.add("boisson_sucree")
    sometimes = {AID_MAP[x] for x in af.get("sometimes", []) if x in AID_MAP}
    if "boisson_sucree" in af.get("liquid", []):
        sometimes.add("cola")                                   # souvent proposé en fin de course : à vérifier
    if not af:                                                  # course inconnue : ravito « classique »
        always = {"pate_fruits", "biscuits_sales", "boisson_sucree"}
        sometimes = {"soupe", "cola"}
    return always, sometimes


def race_plan(legs: list[dict], total_h: float, logs: list[dict] | None, heat: str = "normale",
              meta: dict | None = None) -> dict:
    """`legs` : [{"from", "to", "km", "km0", "km1", "duration_s", "start_s", "aid_at_start": bool,
    "water_only": bool}] dans l'ordre de course. Retourne le plan (forme : voir la vue du simulateur)."""
    meta = meta or {}
    s = summary(logs)
    trained = s["trained_max_g_h"]
    avoid = set(s["avoid"])
    tolerated = [i for i in s["tolerated"] if i in BY_ID]
    lo, hi = target_carbs_g_h(total_h, trained)
    notes: list[str] = []
    if trained:
        aim = int(min(hi, max(lo, trained), MAX_G_H))
        notes.append(f"Objectif {aim} g/h : ton maximum toléré à l'entraînement est {round(trained)} g/h "
                     f"(fourchette conseillée {lo}-{hi} g/h pour {total_h:.1f} h d'effort).".replace(".", ",", 1))
    else:
        hi = min(hi, NO_LOG_CAP)
        aim = min(NO_LOG_AIM, hi)
        notes.append(f"Aucun bilan nutrition encore : je reste prudent à {aim} g/h. Fais une répétition sur tes "
                     "sorties longues (bilan après chaque sortie de plus d'1 h 30) pour savoir ce qui passe.")
    rehearsal = not trained or s["count"] < 2
    if avoid:
        notes.append("Exclu, car mal passé à l'entraînement : "
                     + ", ".join(BY_ID[i].name.lower() for i in avoid if i in BY_ID) + ".")
    always, sometimes = aid_offer(meta)
    fluid_h, na_h = FLUID_ML_H.get(heat, 550), SODIUM_MG_H.get(heat, 450)
    carry_pref = [i for i in tolerated if i in PORTABLE and i not in avoid and i != "gel_cafe"]
    carry_pref += [i for i in DEFAULT_CARRY if i not in carry_pref and i not in avoid]
    carry_pref = carry_pref[:3] or ["gel"]
    iso_id = next((i for i in ("iso_hd", "iso") if i in tolerated and i not in avoid), None)
    if iso_id is None and "iso" not in avoid:
        iso_id = "iso"
    caf_total = 0.0
    out_legs = []
    total = {"carbs_g": 0.0, "fluid_ml": 0.0, "sodium_mg": 0.0, "caffeine_mg": 0.0}
    race_s = max(total_h * 3600, 1)
    for leg in legs:
        h = max(leg["duration_s"] / 3600, 0.05)
        frac = leg["start_s"] / race_s
        late = frac >= 2 / 3
        need_c = aim * h
        # ---- au ravito (au départ du tronçon)
        at: list[dict] = []
        if leg.get("aid_at_start") and not leg.get("water_only"):
            if late:
                wish = ["soupe", "biscuits_sales", "cola", "pate_fruits"]
            elif frac >= 1 / 3 or heat == "chaude":
                wish = ["pain_epices", "biscuits_sales", "boisson_sucree"]
            else:
                wish = ["pate_fruits", "boisson_sucree"]
            wish = [w for w in tolerated if w in always and w not in wish and BY_ID.get(w) and BY_ID[w].digest != "lent"][:1] + wish
            budget = max(need_c * 0.8, 20.0)               # le ravito ne couvre pas plus que le tronçon
            for w in wish:
                if w in avoid or (w not in always and w not in sometimes):
                    continue
                it = _item(w)
                if at and _sum(at, "carbs_g") + it["carbs_g"] > budget:
                    continue
                at.append(it)
            if not late and h > 1.25 and _sum(at, "carbs_g") + BY_ID["pain_epices"].carbs_g <= budget \
                    and "pain_epices" in always and "pain_epices" not in avoid \
                    and not any(i["id"] == "pain_epices" for i in at):
                at.append(_item("pain_epices"))
        at_c = _sum(at, "carbs_g") if at else 0.0
        # ---- liquides : flasques remplies au ravito (réserve de 0,5 L toujours)
        fl_need = max(fluid_h * h - _sum(at, "fluid_ml"), FLASK_ML)
        flasks = max(1, math.ceil(fl_need / FLASK_ML - 0.15))
        fl_note = ""
        if flasks > 3:
            flasks, fl_note = 3, "plus de 1,5 L nécessaires : bois bien au ravito et remplis aussi au point d'eau"
        carry: list[dict] = []
        rem = need_c - at_c
        if iso_id and rem > 25:
            carry.append(_item(iso_id))
            rem -= BY_ID[iso_id].carbs_g
        water_flasks = flasks - (1 if carry else 0)
        # ---- glucides solides / gels
        caf_ok = late and "gel_cafe" not in avoid and caf_total + BY_ID["gel_cafe"].caffeine_mg <= CAFFEINE_MAX_MG
        n = 0
        while rem > 8 and n < 12:
            fid = "gel_cafe" if caf_ok and n == 0 else carry_pref[n % len(carry_pref)]
            if rem < 0.5 * BY_ID[fid].carbs_g:
                break
            caf_ok = caf_ok and fid != "gel_cafe"
            carry.append(_item(fid))
            rem -= BY_ID[fid].carbs_g
            n += 1
        carry = _merge(carry)
        # plafond : haut de la fourchette + 10 g/h, et jamais plus de 120 g/h, ravito compris
        cap = min(MAX_G_H, hi + 10) * h
        while _sum(carry, "carbs_g") + at_c > cap and carry:
            last = carry[-1]
            if last["qty"] > 1:
                for kk in ("carbs_g", "sodium_mg", "fluid_ml", "caffeine_mg"):
                    last[kk] -= last[kk] / last["qty"]
                last["qty"] -= 1
            else:
                carry.pop()
        # ---- sodium
        na = _sum(carry, "sodium_mg") + _sum(at, "sodium_mg")
        salt = 0
        if na_h * h - na > 150 and "sel" not in avoid:
            salt = math.ceil((na_h * h - na) / BY_ID["sel"].sodium_mg)
            carry.append(_item("sel", salt))
            na += BY_ID["sel"].sodium_mg * salt
        caf = _sum(carry, "caffeine_mg") + _sum(at, "caffeine_mg")
        caf_total += caf
        c_tot = _sum(carry, "carbs_g") + at_c
        fluid = water_flasks * FLASK_ML + (FLASK_ML if any(i["id"] in ("iso", "iso_hd") for i in carry) else 0)
        total["carbs_g"] += c_tot
        total["fluid_ml"] += fluid + _sum(at, "fluid_ml")
        total["sodium_mg"] += na
        total["caffeine_mg"] += caf
        lnotes = []
        if fl_note:
            lnotes.append(fl_note)
        if leg.get("water_only"):
            lnotes.append("point d'eau seulement : rien à manger sur place, remplis les flasques")
        if late and any(i["id"] == "soupe" for i in at):
            lnotes.append("soupe et cola selon ce qui est proposé ce jour-là")
        out_legs.append({
            "from": leg["from"], "to": leg["to"], "km0": leg["km0"], "km1": leg["km1"], "km": leg["km"],
            "duration_s": round(leg["duration_s"]), "start_s": round(leg["start_s"]),
            "carbs_g": round(c_tot), "carbs_g_h": round(c_tot / h), "fluid_ml": round(fluid), "flasks": flasks,
            "water_flasks": water_flasks, "sodium_mg": round(na), "caffeine_mg": round(caf),
            "at_aid": [{k2: (round(v, 1) if isinstance(v, float) else v) for k2, v in i.items()} for i in at],
            "carry": [{k2: (round(v, 1) if isinstance(v, float) else v) for k2, v in i.items()} for i in carry],
            "at_aid_label": _label(at), "carry_label": _label(carry), "late": late, "notes": lnotes,
            "line": (f"{leg['from']} → {leg['to']} · {round(leg['km'], 1):g} km · {round(c_tot)} g de glucides : "
                     + (_label(carry) or "rien de plus") + f" ; {flasks} flasque(s)"
                     + (f" ; au ravito : {_label(at)}" if at else "")).replace(".", ",")})
    if iso_id and any(i["id"] == iso_id for leg in out_legs for i in leg["carry"]):
        notes.append(f"Emporte des doses de poudre ({BY_ID[iso_id].name.lower()}) pour remplir une flasque à chaque "
                     "ravito : l'organisateur ne propose que de l'eau et une boisson sucrée.")
    mand = list(meta.get("mandatory") or ["réserve d'eau de 0,5 L minimum", "un produit énergétique",
                                          "gobelet réutilisable"])
    caf_note = ("Caféine seulement dans le dernier tiers (gel caféiné, cola) : pas plus d'environ 3 mg/kg sur la "
                "course, et seulement si tu l'as déjà testée à l'entraînement.")
    if "gel_cafe" in avoid:
        caf_note = "Gel caféiné exclu (mal passé à l'entraînement) : cola au ravito en fin de course si ça te va."
    heat_note = {"chaude": "Chaleur : jusqu'à ~750 ml/h et plus de sodium (soupe, biscuits salés, sel) ; bois selon la "
                           "soif, jamais plus de ~800 ml/h d'eau pure (hyponatrémie).",
                 "fraiche": "Temps frais : ~400 ml/h, bois quand même régulièrement.",
                 "normale": "Eau : ~550 ml/h, à ajuster à la soif."}[heat if heat in FLUID_ML_H else "normale"]
    return {
        "target_g_h": aim, "range_g_h": [lo, hi], "trained_max_g_h": trained, "rehearsal_needed": rehearsal,
        "fluid_ml_h": fluid_h, "sodium_mg_h": na_h, "heat": heat, "notes": notes, "caffeine_note": caf_note,
        "heat_note": heat_note, "mandatory": mand, "avoid": sorted(avoid), "tolerated": tolerated,
        "legs": out_legs, "totals": {k: round(v) for k, v in total.items()},
        "aid_food_label": (meta.get("aid_food") or {}).get("label", ""),
    }


__all__ = ["race_plan", "aid_offer", "MAX_G_H", "FLUID_ML_H", "SODIUM_MG_H"]
