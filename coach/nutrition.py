"""v0.38 — Bilan nutrition des sorties longues (≥ 1 h 30) et conseils par règles.

L'athlète note ce qu'il a pris (palette de `nutrition_foods`), l'eau, sa digestion (1 = très bien,
5 = très mal) et ses symptômes. On en tire des repères déterministes (pas d'appel LLM) :
- des conseils pour la prochaine sortie longue (montée progressive des glucides, alternatives salées,
  sodium…), donnés au coach comme contexte caché et ajoutés à sa réponse si ça s'est mal passé ;
- un résumé (`summary`) : plus haut apport toléré, aliments qui passent, aliments à éviter. Il est
  affiché dans État et donné au coach dans le profil (`prompt_brief`).

Forme d'un bilan (clé `nutrition_logs` du store, liste de dicts ; lue aussi par le plan nutrition
de course) :
    {"date": "YYYY-MM-DD", "session_id": str|None, "activity_id": str|None, "duration_h": float,
     "items": [{"id": str, "qty": float, "carbs_g": float|None}], "water_ml": float,
     "carbs_g_h": float, "sodium_mg_h": float, "fluid_ml_h": float, "digestion": int 1-5,
     "symptoms": [str ids], "comment": str}

Repères : voir les fiches nutrition de `knowledge.py` (Jeukendrup 2014 et 2017, Tiller 2019, Costa 2017,
Hew-Butler 2015). Ce sont des pistes prudentes, pas une prescription médicale.
"""
from __future__ import annotations

from .nutrition_foods import BY_ID, totals

LOGS_KEY = "nutrition_logs"
MIN_DURATION_MIN = 90           # bilan proposé à partir d'1 h 30 (durée réelle, sinon prévue)

SYMPTOMS = {
    "nausee": "nausée",
    "ballonnements": "ballonnements",
    "crampes_estomac": "crampes d'estomac",
    "envie_pressante": "envie pressante",
    "ecoeurement_sucre": "écœurement du sucré",
    "hypo": "coup de barre / hypo",
    "crampes_musculaires": "crampes musculaires",
}
DIGESTION = {1: "très bien", 2: "bien", 3: "moyen", 4: "mal", 5: "très mal"}
GI = {"nausee", "ballonnements", "crampes_estomac", "envie_pressante", "ecoeurement_sucre"}

# libellés courts pour la ligne envoyée au coach (« 3 gels, 1 iso, 2 pâtes de fruits »)
SHORT = {
    "gel": ("gel", "gels"), "gel_hd": ("gel haute dose", "gels haute dose"), "gel_cafe": ("gel caféiné", "gels caféinés"),
    "gommes": ("portion de gommes", "portions de gommes"), "miel": ("miel", "miels"),
    "iso": ("iso", "iso"), "iso_hd": ("iso haute dose", "iso haute dose"), "eau": ("flasque d'eau", "flasques d'eau"),
    "cola": ("cola", "colas"), "soupe": ("soupe", "soupes"), "the_sucre": ("thé sucré", "thés sucrés"),
    "compote": ("compote", "compotes"), "compote_sport": ("compote énergétique", "compotes énergétiques"),
    "pate_fruits": ("pâte de fruits", "pâtes de fruits"), "barre": ("barre", "barres"),
    "barre_cereales": ("barre de céréales", "barres de céréales"), "pain_epices": ("pain d'épices", "pains d'épices"),
    "quatre_quarts": ("part de gâteau", "parts de gâteau"), "banane": ("banane", "bananes"),
    "fruits_secs": ("poignée de fruits secs", "poignées de fruits secs"), "bonbons": ("portion de bonbons", "portions de bonbons"),
    "sandwich": ("demi-sandwich", "demi-sandwichs"), "patate": ("pomme de terre", "pommes de terre"),
    "puree": ("purée", "purées"), "riz": ("bol de riz", "bols de riz"), "fromage": ("fromage", "fromages"),
    "charcuterie": ("charcuterie", "charcuteries"), "biscuits_sales": ("biscuits salés", "biscuits salés"),
    "chips": ("chips", "chips"), "sel": ("capsule de sel", "capsules de sel"),
}
SALTY_IDS = ("soupe", "biscuits_sales", "patate", "puree")
FIBRE_FAT_IDS = {"fruits_secs", "quatre_quarts", "sandwich", "fromage", "charcuterie", "chips", "banane"}
CONCENTRATED_IDS = {"gel_hd", "iso_hd", "gel", "gel_cafe", "miel"}


def _num(x, nd=0) -> str:
    v = round(float(x), nd)
    return (f"{v:.{nd}f}" if nd else str(int(v))).replace(".", ",")


def fmt_duration(h: float) -> str:
    m = int(round(h * 60))
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def clean_items(items: list[dict]) -> list[dict]:
    """Aliments connus, quantités > 0 (au quart près), grammage corrigé optionnel."""
    out = []
    for it in items or []:
        fid = str(it.get("id") or "")
        if fid not in BY_ID:
            continue
        q = round(max(0.0, min(float(it.get("qty") or 0), 60.0)) * 4) / 4
        if q <= 0:
            continue
        c = it.get("carbs_g")
        c = None if c in (None, "") else round(max(0.0, min(float(c), 200.0)), 1)
        out.append({"id": fid, "qty": q, "carbs_g": c})
    return out


def build_log(*, date: str, session_id: str | None, activity_id: str | None, duration_h: float, items: list[dict],
              water_ml: float, digestion: int, symptoms: list[str], comment: str) -> dict:
    """Bilan normalisé, à la forme EXACTE documentée en tête de module."""
    if not 1 <= int(digestion) <= 5:
        raise ValueError("digestion entre 1 (très bien) et 5 (très mal)")
    h = max(0.25, min(float(duration_h), 48.0))
    its = clean_items(items)
    water = round(max(0.0, min(float(water_ml or 0), 20000.0)))
    t = totals(its, h)
    return {"date": date, "session_id": session_id, "activity_id": activity_id, "duration_h": round(h, 2),
            "items": its, "water_ml": float(water), "carbs_g_h": float(t["carbs_g_h"]),
            "sodium_mg_h": float(t["sodium_mg_h"]), "fluid_ml_h": float(round((t["fluid_ml"] + water) / h)),
            "digestion": int(digestion), "symptoms": [s for s in dict.fromkeys(symptoms or []) if s in SYMPTOMS],
            "comment": (comment or "").strip()[:1000]}


def items_text(items: list[dict]) -> str:
    parts = []
    for it in items:
        one, many = SHORT.get(it["id"], (BY_ID[it["id"]].name.lower(),) * 2)
        q = it["qty"]
        parts.append(f"{_num(q, 0 if q == int(q) else 1)} {many if q >= 2 else one}")
    return ", ".join(parts)


def athlete_line(log: dict, title: str | None = None) -> str:
    """« Sortie longue 2 h 10 : 62 g/h (3 gels, 1 iso, 2 pâtes de fruits), digestion 2/5, nausée »."""
    what = items_text(log["items"])
    syms = ", ".join(SYMPTOMS[s] for s in log["symptoms"])
    line = (f"{title or 'Sortie longue'} {fmt_duration(log['duration_h'])} : {_num(log['carbs_g_h'])} g/h"
            + (f" ({what})" if what else " (rien mangé)") + f", digestion {log['digestion']}/5"
            + (f", {syms}" if syms else ""))
    if log.get("water_ml"):
        line += f", {_num(log['fluid_ml_h'])} ml/h bus"
    if log.get("comment"):
        line += f". {log['comment']}"
    return line


def _good(log: dict) -> bool:
    return log["digestion"] <= 2 and not (set(log["symptoms"]) & (GI | {"hypo"}))


def _bad(log: dict) -> bool:
    return log["digestion"] >= 4 or bool(set(log["symptoms"]) & GI)


def advice(log: dict, history: list[dict] | None = None) -> list[str]:
    """Pistes déterministes pour la prochaine sortie longue (français, prudentes). `history` = bilans
    précédents (sans `log`)."""
    hist = [h for h in (history or []) if h is not log]
    ids = {it["id"] for it in log["items"]}
    sy = set(log["symptoms"])
    g = log["carbs_g_h"]
    prev_good = max((h["carbs_g_h"] for h in hist if _good(h)), default=0.0)
    out: list[str] = []
    if "hypo" in sy or (g < 40 and log["duration_h"] >= 1.5):
        nxt = int(min(max(g, 30) + 10, 90))
        out.append(f"Apport bas ({_num(g)} g/h){' et coup de barre' if 'hypo' in sy else ''} : monte progressivement, "
                   f"environ +10 g/h par sortie longue (vise ~{nxt} g/h la prochaine fois), en prenant quelque chose "
                   "toutes les 20-30 min dès le début : c'est l'entraînement de l'intestin.")
    if g > 90 and prev_good < 80:
        out.append(f"{_num(g)} g/h sans tolérance déjà éprouvée au-delà de 80 g/h : redescends vers 70-80 g/h et "
                   "remonte par paliers ; au-delà de 60 g/h, mélange glucose (maltodextrine) et fructose.")
    if sy & {"nausee", "ecoeurement_sucre"}:
        out.append("Nausée ou écœurement du sucré : alterne avec du salé (soupe, biscuits salés, pomme de terre), "
                   "dilue davantage tes boissons, espace les gels concentrés et prends-les avec quelques gorgées d'eau.")
    if sy & {"envie_pressante", "ballonnements"}:
        tips = sorted(ids & FIBRE_FAT_IDS)
        what = (" (ici : " + ", ".join(SHORT.get(i, (i, i))[1] for i in tips) + ")") if tips else ""
        out.append("Envie pressante ou ballonnements : moins de fibres, de gras et de fruits secs" + what
                   + " pendant l'effort et la veille au soir ; vérifie la part de fructose (beaucoup de fruits, "
                   "compotes et miel) et teste un produit à base de maltodextrine.")
    if "crampes_estomac" in sy:
        out.append("Crampes d'estomac : petites prises régulières plutôt que de grosses d'un coup, boissons moins "
                   "concentrées, et rien de nouveau le jour d'une course.")
    if "crampes_musculaires" in sy:
        na = log["sodium_mg_h"]
        out.append(f"Crampes musculaires : vérifie le sodium ({_num(na)} mg/h) — vise environ 300-600 mg par litre bu, "
                   "plus par forte chaleur (boisson iso, capsules de sel, soupe). La fatigue et l'allure jouent aussi : "
                   "le sel n'explique pas tout.")
    fl = log["fluid_ml_h"]
    if fl > 900:
        out.append(f"Tu as bu beaucoup ({_num(fl)} ml/h) : bois selon la soif et la chaleur, sans te forcer — trop "
                   "boire d'eau pauvre en sel expose à l'hyponatrémie.")
    elif log["water_ml"] and fl < 300 and log["duration_h"] >= 2:
        out.append(f"Peu bu ({_num(fl)} ml/h) : sur une sortie longue, bois régulièrement selon la soif "
                   "(souvent 400-800 ml/h selon la chaleur).")
    if _good(log) and g >= 40 and g > prev_good:
        out.append(f"Digestion bonne à {_num(g)} g/h : c'est ton nouveau maximum toléré, garde ce mélange comme base.")
    elif _good(log) and not out:
        out.append("Bonne digestion : garde ces produits, ils passent bien chez toi.")
    if _bad(log) and not any(x.startswith(("Nausée", "Envie", "Crampes d'estomac")) for x in out):
        out.append("Digestion difficile : à la prochaine sortie longue, garde un seul changement à la fois "
                   "(quantité OU produit) pour savoir ce qui ne passe pas.")
    return out


def summary(logs: list[dict] | None) -> dict:
    """{trained_max_g_h, tolerated: [ids], avoid: [ids], last: [...]} (plus récent d'abord)."""
    logs = sorted(logs or [], key=lambda x: (x.get("date") or ""))
    good = [x for x in logs if _good(x)]
    trained = max((x["carbs_g_h"] for x in good), default=None)
    count: dict[str, int] = {}
    for x in good:
        for it in x["items"]:
            count[it["id"]] = count.get(it["id"], 0) + 1
    tolerated = sorted(count, key=lambda i: (-count[i], i))
    badc: dict[str, int] = {}
    for x in logs:
        if _bad(x):
            for it in x["items"]:
                if it["id"] not in count:
                    badc[it["id"]] = badc.get(it["id"], 0) + 1
    avoid = sorted(badc, key=lambda i: (-badc[i], i))
    last = [{"date": x["date"], "session_id": x.get("session_id"), "duration_h": x["duration_h"],
             "carbs_g_h": x["carbs_g_h"], "sodium_mg_h": x["sodium_mg_h"], "fluid_ml_h": x["fluid_ml_h"],
             "digestion": x["digestion"], "symptoms": x["symptoms"]} for x in reversed(logs[-6:])]
    return {"trained_max_g_h": trained, "tolerated": tolerated, "avoid": avoid, "last": last, "count": len(logs)}


def prompt_brief(logs: list[dict] | None) -> str:
    """Une phrase pour le profil donné au coach (vide sans bilan)."""
    s = summary(logs)
    if not s["count"]:
        return ""
    name = lambda i: BY_ID[i].name.lower() if i in BY_ID else i    # noqa: E731
    parts = [f"Nutrition à l'effort ({s['count']} bilan(s) de sorties longues)"]
    if s["trained_max_g_h"]:
        parts.append(f"max toléré {_num(s['trained_max_g_h'])} g/h")
    if s["tolerated"]:
        parts.append("passe bien : " + ", ".join(name(i) for i in s["tolerated"][:5]))
    if s["avoid"]:
        parts.append("à éviter : " + ", ".join(name(i) for i in s["avoid"][:4]))
    x = s["last"][0]
    parts.append(f"dernier : {_num(x['carbs_g_h'])} g/h, digestion {x['digestion']}/5"
                 + (" (" + ", ".join(SYMPTOMS[k] for k in x["symptoms"]) + ")" if x["symptoms"] else ""))
    return " ; ".join(parts) + "."


def needs_proposals(log: dict) -> bool:
    """Ça s'est mal passé : la réponse du coach doit contenir des propositions concrètes."""
    return _bad(log) or "hypo" in log["symptoms"] or "crampes_musculaires" in log["symptoms"]


__all__ = ["LOGS_KEY", "MIN_DURATION_MIN", "SYMPTOMS", "DIGESTION", "build_log", "athlete_line", "advice",
           "summary", "prompt_brief", "needs_proposals", "fmt_duration", "SALTY_IDS"]
