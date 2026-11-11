"""Bac à sable : athlète de démo + LLM heuristique hors-ligne + agenda en mémoire.

Utilisé par le simulateur terminal (`python -m coach.demo`) et par le bot
Telegram de test. Deux modes :
- hors-ligne (défaut) : un "LLM" à base de règles, 0 €, déterministe. Il
  simule volontairement une MAUVAISE proposition quand on parle de
  "rattraper", pour montrer les garde-fous à l'œuvre.
- --claude : vrais appels Claude (Haiku 4.5 + Sonnet 5), clé ANTHROPIC_API_KEY.

L'athlète de démo est FICTIF : Diagonale des Fous le 15/10/2026, en affûtage.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .adapters.calendar import InMemoryCalendar
from .graph import AthleteContext, Deps, build_graph
from .guardrails import HARD_INTENSITIES, apply_changes
from .knowledge import KNOWLEDGE
from .llm import Usage
from .load_engine import LoadState
from .rag import SEED_CORPUS, TagRetriever
from .schemas import CoachDecision, Pain, PlanChange, PlannedSession, Signals

DIAG_2026 = date(2026, 10, 15)
LABELS = {"rest": "Repos", "z1": "Footing Z1", "z2": "Endurance Z2", "tempo": "Tempo",
          "threshold": "Seuil", "vo2": "VMA", "long": "Sortie longue", "hills": "Côtes",
          "race": "COURSE"}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


# --------------------------------------------------------------------------- #
# Athlète de démo
# --------------------------------------------------------------------------- #
def demo_plan(today: date, aid: str, race_date: date = DIAG_2026, taper_days: int = 14) -> list[PlannedSession]:
    """Semaine type, affûtée si la course approche : volume réduit progressivement
    (jusqu'à -50 % la veille), intensité conservée ; course le jour J, repos après."""
    spec = [("threshold", 70, 0, 80, "3 x 12 min au seuil, récup 3 min"),
            ("z2", 60, 150, 50, "Endurance fondamentale"),
            ("rest", 0, 0, 0, ""),
            ("long", 180, 1500, 170, "Sortie longue vallonnée, marche active en côte"),
            ("z2", 75, 200, 60, "Endurance, relâché"),
            ("hills", 70, 600, 85, "8 x 3 min en côte, récup descente"),
            ("z1", 45, 0, 30, "Footing de décrassage")]
    out = []
    for i in range(7):
        day = today + timedelta(days=i)
        sid, j = f"{aid}-{day}", (race_date - day).days
        if j == 0:
            out.append(PlannedSession(session_id=sid, day=day, intensity="race", duration_min=2400,
                                      elevation_gain_m=10_200, planned_load=900, is_race=True,
                                      description="Diagonale des Fous, départ 22 h"))
            continue
        k, m, dp, ld, desc = spec[(day.weekday() - 2) % 7]       # mercredi = séance au seuil
        if j < 0:
            k, m, dp, ld, desc = "rest", 0, 0, 0, "Récupération post-course"
        elif j <= taper_days:
            f = 1 - 0.5 * (1 - j / taper_days)
            m, dp, ld = int(m * f), int(dp * f), round(ld * f, 1)
        out.append(PlannedSession(session_id=sid, day=day, intensity=k, duration_min=m,
                                  elevation_gain_m=dp, planned_load=ld, description=desc))
    return out


@dataclass
class DemoRepo:
    today: date
    race_date: date = DIAG_2026
    plans: dict[str, list[PlannedSession]] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)

    def plan(self, aid: str) -> list[PlannedSession]:
        if aid not in self.plans:
            self.plans[aid] = demo_plan(self.today, aid, self.race_date)
            self.baselines[aid] = sum(p.planned_load for p in self.plans[aid])
        return self.plans[aid]

    def get(self, aid: str) -> AthleteContext:
        return AthleteContext(
            athlete_id=aid, race_date=self.race_date,
            load=LoadState(ctl=95, atl=90, tsb=5, acwr=0.9, a7=90, c28=100),
            plan_next_7d=list(self.plan(aid)), baseline_week_load=self.baselines[aid],
            profile_summary="Ultra-traileur expérimenté, discipliné, tendance à vouloir en faire plus.",
            pre_taper_week_load=600, pre_taper_sessions_per_week=6)

    def apply(self, aid: str, changes: list[PlanChange]) -> None:
        self.plans[aid] = apply_changes(self.plan(aid), changes)

    def reset(self, aid: str) -> None:
        self.plans.pop(aid, None)


class DemoCalendar(InMemoryCalendar):
    """Agenda en mémoire qui met aussi à jour le plan de l'athlète."""

    def __init__(self, repo: DemoRepo):
        super().__init__()
        self.repo = repo

    def apply(self, athlete_id, changes):
        ops = super().apply(athlete_id, changes)
        self.repo.apply(athlete_id, changes)
        return ops


# --------------------------------------------------------------------------- #
# "LLM" heuristique hors-ligne
# --------------------------------------------------------------------------- #
_BODY = r"(genou|cheville|tendon|achille|mollet|hanche|pied|dos|ischio|quadri|cuisse|tibia|fessier|aine)"
_PAIN_MARK = r"(mal|douleur|tire|gonfle|coince|tordu|point|bless|pince)"
_THIRD = r"(ma fille|mon fils|ma femme|mon mari|mes enfants|ma copine|mon copain|le petit|la petite|mon pote)"
_ALCOHOL = r"(alcool|\bbu\b|bière|biere|vin |ap[ée]ro|cocktail|whisky|rhum|verres|cuite|bourr|gueule de bois|arros[ée])"
_ALCOHOL_HEAVY = r"(beaucoup d.alcool|beaucoup bu|trop bu|pas mal bu|cuite|bourr|gueule de bois|arrach|bien arros)"
_CONFLICT = r"(patience|dispute|engueul|tendu|stress|[ée]nerv|à bout|a bout|craqu|ras.le.bol|d[ée]borde|p[èe]te un c[âa]ble)"
_TRAINING_Q = (r"(entra[iî]n|séance|seance|plan|allure|récup|recup|affût|affut|diag|footing|sortie|"
               r"courir|course|km|forme|rajouter|en faire plus|charge|volume|fatigu|"
               r"glucide|\bgels?\b|nutrition|manger|boire|\bbois\b|ravito|hydrat|caf[ée]ine|vfc|renfo|"
               r"séance de seuil|au seuil|seuil (lactique|anaérobie|ventilatoire|mer|jeu|ven|lun|mar|sam|dim))")
_IDIOMS = r"((me|nous) fai(t|re) courir|courir partout|cavaler|seuil de (patience|tolérance|tolerance))"
# NB : « seuil » seul est ambigu (« seuil de patience ») : on n'accepte que le sens entraînement.


def heuristic_signals(text: str, today: date) -> Signals:
    t = text.lower()
    sig = Signals()
    if re.search(_BODY, t) and re.search(_PAIN_MARK, t):
        # « 7/10 » près de la douleur ; pas une date (« 28/10 ») ni l'effort ressenti (« RPE 6/10 »)
        m = next((x for x in re.finditer(r"(?<![\d/])(10|\d)\s*/\s*10(?![\d/])", t)
                  if not re.search(r"(rpe|effort|ressenti)\s*:?\s*$", t[max(0, x.start() - 12):x.start()])), None)
        sig.pain = [Pain(location=re.search(_BODY, t).group(1),
                         severity_0_10=int(m.group(1)) if m else 4)]
    if re.search(r"(grippe|fi[eè]vre|rhume|angine|covid|gastro|mal de gorge|malade)", t) \
            and not re.search(_THIRD, t):
        sig.illness_symptoms = [w for w in ("grippe", "fièvre", "rhume", "angine", "gastro", "mal de gorge")
                                if w in t] or ["malade"]
    if re.search(r"(épuis|epuis|vidé|vide |hs\b|à plat|a plat)", t):
        sig.fatigue_1_5 = 5
    elif re.search(r"(crevé|creve|cuit|fatigu|jambes lourdes|lourd)", t):
        sig.fatigue_1_5 = 4
    if re.search(r"(pas dormi|mal dormi|nuit blanche|nuit courte|toute la nuit)", t):
        sig.sleep_quality_1_5 = 1
    elif re.search(r"(jusque tard|jusqu.à tard|couché tard|couche tard|rentré tard|peu dormi)", t):
        sig.sleep_quality_1_5 = 2
    m = re.search(r"(?:dormi|sommeil)\D{0,6}(\d{1,2}) ?h|(\d{1,2}) ?h(?:eures?)? de sommeil", t)
    if m:
        sig.sleep_hours = float(m.group(1) or m.group(2))
    if re.search(_ALCOHOL, t + " "):
        sig.alcohol = "heavy" if re.search(_ALCOHOL_HEAVY, t) else "moderate"
    if re.search(_CONFLICT, t):
        sig.life_stress_1_5, sig.emotional_distress_1_5 = 4, 3
    elif re.search(_THIRD, t):
        sig.life_stress_1_5 = 3
    literal = re.sub(_IDIOMS, " ", t)          # « elle me fait courir partout » ≠ entraînement
    if "?" in t and not re.search(_TRAINING_Q, literal):
        sig.off_topic = True
    m = re.search(r"rat[ée]\w* (\d+) (jours|séances|seances)", t)
    if m:
        sig.unavailable_dates = [today - timedelta(days=i + 1) for i in range(int(m.group(1)))]
    m = re.search(r"rpe ?(\d{1,2})", t)
    if m:
        sig.rpe_last_session = max(1, min(int(m.group(1)), 10))
    if "?" in t:
        sig.athlete_question = text
    return sig


def _fmt(s: PlannedSession) -> str:
    return f"{LABELS[s.intensity]} {JOURS[s.day.weekday()]}"


def heuristic_decision(payload: dict) -> CoachDecision:
    plan = [PlannedSession.model_validate(p) for p in payload["plan_next_7d"]]
    sig = Signals.model_validate(payload["signals"])
    msg = payload["athlete_message"].lower()
    j = payload.get("days_to_race")

    if msg.startswith("j'ai déplacé"):         # v0.37 : séance glissée dans Semaine (déjà enregistrée)
        m = re.search(r"à surveiller : (.*?)(?:\. Ces points|\. Consigne|\.\])", payload["athlete_message"])
        return CoachDecision(changes=[], message_to_athlete="C'est noté 👍 Ton plan et ton agenda sont à jour."
                             + (f" À surveiller : {m.group(1)}. Écoute tes sensations." if m else ""))
    if "[bilan nutrition" in msg:              # v0.38 : bilan nutrition d'une sortie longue
        bad = "mal passé" in msg
        return CoachDecision(changes=[], message_to_athlete=(
            "Merci pour ce bilan. On ajuste ta nutrition pas à pas, une seule chose à la fois." if bad else
            "Merci pour ce bilan 👍 Garde ce qui a bien marché pour tes prochaines sorties longues."))

    if "rattrap" in msg and payload.get("critic_feedback"):   # v0.40 : le critique a relu le brouillon
        return CoachDecision(changes=[], message_to_athlete=(
            "Non, pas de rattrapage : les séances manquées sont derrière toi, les rattraper ajouterait de la fatigue "
            "sans bénéfice. On reprend le plan tel quel, à commencer par la prochaine séance."))
    if "rattrap" in msg:        # proposition volontairement mauvaise -> démo des garde-fous (et du critique en v0.40)
        base = plan[1].day if len(plan) > 1 else plan[0].day
        extra = [PlannedSession(session_id=f"{plan[0].session_id}-x{i}", day=base + timedelta(days=i),
                                intensity="vo2", duration_min=80, planned_load=150,
                                description="Séance de rattrapage") for i in range(2)]
        return CoachDecision(changes=[PlanChange(action="add", session=s, reason="rattrapage")
                                      for s in extra],
                             message_to_athlete="On rattrape avec 2 grosses séances de VMA !")

    worst = max((p.severity_0_10 for p in sig.pain), default=0)
    if worst > 3 or sig.illness_symptoms:
        changes = [PlanChange(action="modify", reason="douleur/maladie", session=s.model_copy(update={
            "intensity": "z1", "duration_min": min(s.duration_min, 40), "elevation_gain_m": 0,
            "planned_load": min(s.planned_load, 25.0), "description": "Footing très facile ou repos"}))
            for s in plan if s.intensity in HARD_INTENSITIES]
        cause = f"ta douleur ({sig.pain[0].location}, {worst}/10)" if worst > 3 else "tes symptômes"
        if not changes:
            return CoachDecision(changes=[], message_to_athlete=(
                f"Avec {cause}, on reste sur du très facile (c'est déjà le cas cette semaine). "
                "Repos complet si ça augmente, et consulte si ça persiste 48 h."))
        return CoachDecision(
            changes=changes, is_major_change=len(changes) > 2,
            message_to_athlete=(f"Avec {cause}, je passe toutes les séances intenses de la semaine "
                                f"en footing très facile. Si ça persiste 48 h, consulte."))

    if sig.alcohol == "heavy":
        soon = [p for p in plan if (p.day - plan[0].day).days <= 1 and p.intensity != "rest"]
        changes = [PlanChange(action="modify", reason="alcool", session=p.model_copy(update={
            "intensity": "z1", "duration_min": min(p.duration_min, 40), "elevation_gain_m": 0,
            "planned_load": min(p.planned_load, 25.0),
            "description": "Très facile ou repos (récupération après alcool)"})) for p in soon]
        return CoachDecision(changes=changes, message_to_athlete=(
            "Demain, pas d'intensité : 30 à 40 min très facile au maximum, ou repos si tu te sens mal "
            "au réveil. Bois beaucoup d'eau d'ici là. La séance intense saute, on ne la rattrape pas."))

    if (sig.fatigue_1_5 or 0) >= 4 or (sig.sleep_quality_1_5 or 5) <= 2 \
            or (sig.sleep_hours is not None and sig.sleep_hours < 5):
        target = next((s for s in plan if s.intensity in HARD_INTENSITIES
                       and (s.day - plan[0].day).days <= 1), None)
        if target:
            easy = target.model_copy(update={"intensity": "z1", "duration_min": 40, "elevation_gain_m": 0,
                                             "planned_load": 30.0, "description": "Footing cool, récupération"})
            return CoachDecision(changes=[PlanChange(action="modify", session=easy, reason="fatigue")],
                                 message_to_athlete=f"Bien vu de me le dire. Je remplace ta séance "
                                                    f"({_fmt(target)}) par 40 min de footing cool.")
        return CoachDecision(changes=[], message_to_athlete="Noté. Rien d'intense avant 48 h : "
                                                            "écoute ton corps et dors.")

    if (sig.life_stress_1_5 or 0) >= 4:
        target = next((p for p in plan if p.intensity in HARD_INTENSITIES
                       and (p.day - plan[0].day).days == 0), None)
        empathy = ("Désolé, ça a l'air pesant en ce moment. " + (
            "Pour ce qui se passe à la maison, je ne suis pas le mieux placé : en parler à un proche "
            "ou à un professionnel peut vraiment aider. " if sig.off_topic else ""))
        if target:
            easy = target.model_copy(update={"intensity": "z2", "duration_min": min(target.duration_min, 50),
                                             "planned_load": min(target.planned_load, 40.0),
                                             "description": "Footing relâché, sans chrono"})
            return CoachDecision(changes=[PlanChange(action="modify", session=easy, reason="stress")],
                                 is_major_change=True, message_to_athlete=empathy + (
                                     f"Côté entraînement, je te propose de remplacer ta séance ({_fmt(target)}) "
                                     "par un footing relâché de 50 min : ça aide souvent à décompresser."))
        return CoachDecision(changes=[], message_to_athlete=empathy + (
            "Côté entraînement, rien d'intense aujourd'hui : un footing relâché si tu en as envie."))

    if sig.off_topic:
        return CoachDecision(changes=[], message_to_athlete=(
            "Là-dessus je ne suis pas le mieux placé : je suis ton coach d'entraînement. "
            "Si ça a un impact sur ta forme ou ton planning, dis-le-moi et j'adapte."))

    if re.search(r"(en forme|plus|rajout|ajout)", msg) and "?" in msg:
        extra = f" À J-{j}," if j is not None else ""
        return CoachDecision(changes=[], message_to_athlete=f"Non.{extra} on n'ajoute rien : "
                             "l'affûtage paie le jour J. Garde cette énergie.")

    nxt = next((s for s in plan if s.intensity != "rest"), None)
    return CoachDecision(changes=[], message_to_athlete="Noté 👍" + (
        f" Prochaine séance : {_fmt(nxt)}, {nxt.duration_min} min." if nxt else ""))


@dataclass
class HeuristicLLM:
    today: date
    usage_log: list[Usage] = field(default_factory=list)

    def structured(self, *, node, tier, system, user, schema):
        self.usage_log.append(Usage(node=node, tier=tier, input_tokens=len(system + user) // 4,
                                    output_tokens=120 if tier == "small" else 450))
        if node in ("onboarding", "onboard_recap"):   # hors ligne : le brouillon déterministe tel quel
            return schema.model_validate({"message": json.loads(user)["draft"]})
        if node in ("onboard_goal", "onboard_turn", "onboard_profile", "prefs_edit"):  # hors ligne : règles
            from .onboarding import parse_goal, parse_profile, parse_turn
            d = json.loads(user)
            out = (parse_goal(d["message"], self.today, d.get("candidats")) if node == "onboard_goal"
                   else parse_profile(d["message"], self.today) if node == "onboard_profile"
                   else parse_turn(d["message"], self.today))
            return schema.model_validate(out.model_dump())
        if node == "onboard_week_max":               # v0.49 : volume maximum par semaine, par règles
            from .week_max import parse_week_max
            return schema.model_validate(parse_week_max(json.loads(user)["message"]).model_dump())
        if node == "goal_edit":                      # v0.25 : correction d'échéance, par règles
            from .onboarding import Event, parse_goal_edit
            d = json.loads(user)
            out = parse_goal_edit(d["message"], [Event.model_validate(e) for e in d["echeances"]], self.today)
            return schema.model_validate(out.model_dump())
        from .agents.offline import TEAM_NODES, offline_structured, team_synthesis
        if node in TEAM_NODES:                       # v0.40 : superviseur, critique, rapport de l'Œil du coach
            return offline_structured(node, user, schema)
        if node == "extract":
            out = heuristic_signals(user.split("Message:", 1)[-1], self.today)
        else:
            payload = json.loads(user)
            out = team_synthesis(heuristic_decision(payload), payload)
        return schema.model_validate(out.model_dump())

    def tool_loop(self, *, node, tier, system, user, tools, schema, max_steps=4):
        """v0.40 : spécialiste hors ligne (outils réellement exécutés, constats déterministes)."""
        from .agents.offline import offline_agent
        res = offline_agent(node, user, tools, schema)
        # estimation d'une boucle réelle : 2 allers-retours (outils puis réponse finale), ~300 tokens par résultat d'outil
        self.usage_log.append(Usage(node=node, tier=tier, output_tokens=120 + 60 * len(res.tool_calls),
                                    input_tokens=2 * len(system + user) // 4 + 300 * len(res.tool_calls)))
        return res


# --------------------------------------------------------------------------- #
# Assemblage + affichage
# --------------------------------------------------------------------------- #
PRICE_USD = {"small": (1.0, 5.0), "main": (2.0, 10.0)}    # Haiku 4.5 / Sonnet 5, $/MTok
USD_EUR = 0.86                                             # hypothèse (cf. finops.py)


def build_sandbox(today: date | None = None, use_claude: bool = False, checkpointer=None, llm=None,
                  agents: str | None = None, data=None):
    from .checkpoint import memory_saver
    today = today or date.today()
    repo = DemoRepo(today)
    if llm is None and use_claude:
        from .llm import AnthropicLLM
        llm = AnthropicLLM()
    elif llm is None:
        llm = HeuristicLLM(today)
    from .agents.data import NullData
    data = data or NullData(lambda: today, goal={"name": "Diagonale des Fous", "date": DIAG_2026.isoformat(), "km": 175,
                                         "dplus": 10200, "kind": "trail"})
    deps = Deps(llm=llm, retriever=TagRetriever(SEED_CORPUS + KNOWLEDGE), calendar=DemoCalendar(repo),
                repo=repo, today=lambda: today, data=data, agents=agents)
    return build_graph(deps, checkpointer=checkpointer or memory_saver()), deps


def cost_eur(usages: list[Usage]) -> float:
    usd = 0.0
    for u in usages:
        pin, pout = PRICE_USD[u.tier]
        fresh_in = u.input_tokens - u.cache_read
        usd += (fresh_in * pin + u.cache_read * pin * 0.1 + u.output_tokens * pout) / 1e6
    return usd * USD_EUR


def format_agenda(plan: list[PlannedSession]) -> str:
    lines = []
    for s in plan:
        if s.intensity == "rest":
            lines.append(f"  {JOURS[s.day.weekday()]} {s.day:%d/%m}  Repos")
            continue
        dp = f", D+ {s.elevation_gain_m} m" if s.elevation_gain_m else ""
        lines.append(f"  {JOURS[s.day.weekday()]} {s.day:%d/%m}  {LABELS[s.intensity]:<14} "
                     f"{s.duration_min:>3} min{dp}  (charge {s.planned_load:.0f})")
    return "\n".join(lines)


def format_trace(trace: list, usages: list[Usage], estimated: bool) -> str:
    parts = []
    for node, upd in trace:
        if node == "triage":
            parts.append(f"tri={upd['route'].value}" + (" ⚠URGENCE" if upd.get("emergency") else ""))
        elif node == "guard":
            v = upd["verdict"]
            if v.allowed and not v.violations:
                parts.append("garde-fous ✅")
            else:
                tags = ", ".join(f"{'⛔' if x.severity == 'block' else '⚠'}{x.rule_id}" for x in v.violations)
                parts.append(("garde-fous ✅ " if v.allowed else "garde-fous ") + tags)
        elif node == "calendar":
            ops = upd.get("calendar_ops", [])
            parts.append(f"agenda: {len(ops)} modif." if ops else "agenda inchangé")
        elif node in ("extract", "coach"):
            parts.append(f"{node} (LLM)")
        elif node == "route":                       # v0.40 : équipe d'agents
            r = upd.get("routing") or {}
            from .agents.roster import TEAM
            who = ", ".join(TEAM[k].label for k in r.get("specialists", [])) or "coach seul"
            parts.append(f"superviseur ({r.get('by', '?')}) : {who}")
        elif node == "specialist":
            for n in upd.get("notes") or []:
                parts.append(f"{n['icon']} {n['label']} ({len(n['tools'])} outils, risque {n['risk_level']})"
                             + (" ⛔veto" if n["veto"] else ""))
        elif node == "team":
            continue
        elif node == "critic":
            c = upd.get("critic") or {}
            parts.append("critique ✅" if c.get("verdict") == "ok" else
                         "critique ↩ révision" if c.get("action") == "revise" else "critique ⚠ réserves")
        else:
            parts.append(node)
    if usages:
        tok = sum(u.input_tokens + u.output_tokens for u in usages)
        parts.append(f"{len(usages)} appel(s) LLM, {tok} tokens{' estimés' if estimated else ''}, "
                     f"~{cost_eur(usages) * 100:.2f} c€")
    else:
        parts.append("0 token")
    return " → ".join(parts)
