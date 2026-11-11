"""Orchestration LangGraph : 1 LLM "cerveau conversationnel" + outils déterministes.

    START -> triage ─┬─ noise ─────> ack ───────────────────────────────> END
                     ├─ emergency ─> emergency ──────────────┐
                     └─ coaching / red_flag                  │
                          -> extract (small LLM)             │
                          -> evidence (RAG, 0 token)         │
                          -> coach (main LLM) -> guard (code)│
                                 ^   │ violations & <2 essais│
                                 └───┤                       │
                                     ├─ ok ─> confirm (HITL si majeur)
                                     └─ ko x2 -> fallback    │
                                                   └─> calendar -> END

Seuls `extract` et `coach` consomment des tokens. Tout le reste est testable
unitairement, rejouable, et gratuit.

v0.40 (`COACH_AGENTS=multi`, défaut) : entre `evidence` et `coach`, une ÉQUIPE d'agents (coach/agents/) :
superviseur (routage) -> spécialistes outillés EN PARALLÈLE (Send) -> fan-in -> coach (synthèse) -> critique
(LLM-as-judge, 1 révision max) -> guard. `COACH_AGENTS=single` garde le pipeline ci-dessus (évaluation A/B).

NB : la charge (CTL/ATL/TSB) n'est PAS calculée ici. Elle est recalculée de
façon événementielle à l'arrivée d'une activité (webhook montre -> load_engine
-> base), puis simplement lue par `AthleteRepo`. Un message ne déclenche donc
jamais de parsing FIT.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Annotated, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .adapters.calendar import CalendarPort
from .agents.team import COACH_TEAM_ADDENDUM, add_notes, agents_mode, make_team_nodes, team_payload
from .classifier import Consent, rule_consent
from .guardrails import PlanContext, apply_changes, cap_peak_sessions, check, safe_fallback, tolerate_preexisting
from .llm import LLMPort
from .load_engine import LoadState
from .rag import RetrieverPort
from .schemas import CoachDecision, GuardrailVerdict, InboundMessage, PlanChange, PlannedSession, Route, Signals
from .triage import is_distress, is_emergency, triage

MAX_COACH_ATTEMPTS = 2
# Transparence : l'athlète sait POURQUOI la proposition a été refusée.
RULE_LABELS = {
    "R01_pain": "douleur au-delà du seuil", "R02_illness": "symptômes de maladie",
    "R03_acwr_danger": "hausse de charge trop brutale", "R03_acwr_vigilance": "surcharge hors bloc de choc",
    "R05_catch_up": "pas de rattrapage", "R06_taper_volume": "volume trop élevé pour l'affûtage",
    "R07_recovery": "récupération compromise (alcool / sommeil)", "R08_life_stress": "stress de vie élevé",
    "R09_peak_session": "sortie trop longue par rapport au dernier mois",
    "R10_week_max": "au-delà de ton volume maximum par semaine",
}


# --------------------------------------------------------------------------- #
# Dépendances injectées (ports) -> swap Mock / prod sans toucher au graphe
# --------------------------------------------------------------------------- #
@dataclass
class AthleteContext:
    athlete_id: str
    race_date: date | None
    load: LoadState
    plan_next_7d: list[PlannedSession]
    baseline_week_load: float
    profile_summary: str        # mémoire long terme compressée (pas l'historique brut)
    shock_block_active: bool = False
    pre_taper_week_load: float | None = None
    pre_taper_sessions_per_week: float | None = None
    longest_run_30d_min: float | None = None
    week_max: object | None = None      # v0.49 : week_max.WeekMax (volume maximum déclaré)


class AthleteRepo(Protocol):
    def get(self, athlete_id: str) -> AthleteContext: ...


@dataclass
class Deps:
    llm: LLMPort
    retriever: RetrieverPort
    calendar: CalendarPort
    repo: AthleteRepo
    today: Callable[[], date] = date.today
    router: Callable[[str], Route] = triage          # ou ThresholdRouter(JevClassifier(), ...)
    consent: Callable[[str], Consent] = rule_consent  # candidat JEV n°2
    persona: Callable[[], str] = lambda: ""          # v0.36 : bloc « personnalité du coach » (ton seulement)
    # v0.38 : règles bloquantes que l'athlète a acceptées en déplaçant lui-même une séance (le temps de la
    # réaction du coach ; "*" = toutes) : déjà présentes dans le plan, elles ne bloquent pas une réponse qui
    # ne les crée pas (pas d'allègement imposé en repli) ; une NOUVELLE violation bloque toujours
    tolerated: Callable[[], set] = lambda: set()
    # v0.40 : équipe d'agents. `agents` : "multi" | "single" | None (= variable COACH_AGENTS, défaut multi) ;
    # `data` : port de données des outils des spécialistes (coach/agents/data.py), None = données minimales
    agents: str | None = None
    data: object | None = None
    # v0.52 : derniers messages du fil [{role, text}] : le coach répond dans la continuité de la discussion
    history: Callable[[], list] = lambda: []


class CoachState(TypedDict, total=False):
    inbound: InboundMessage
    route: Route
    emergency: bool
    distress: bool
    signals: Signals | None
    evidence: list[dict]
    decision: CoachDecision | None
    verdict: GuardrailVerdict | None
    attempts: int
    final_changes: list[PlanChange]
    calendar_ops: list[str]
    reply: str
    # v0.40 : équipe d'agents
    routing: dict | None
    notes: Annotated[list, add_notes]
    team_cards: list[dict]
    critic: dict | None
    spec: str
    spec_reason: str


# --------------------------------------------------------------------------- #
# Prompts (statiques => cacheables, partagés par tous les athlètes)
# --------------------------------------------------------------------------- #
DISTRESS_REPLY = (
    "Ce que tu écris m'inquiète, et c'est plus important que l'entraînement. "
    "Si tu te sens en danger ou si quelqu'un risque d'être blessé, appelle le 17 ou le 112 maintenant. "
    "Si tu as des idées noires, le 3114 répond 24 h/24, gratuitement. "
    "Je mets le plan de côté : parle à quelqu'un de confiance, on reprendra quand tu voudras.")

EXTRACT_SYSTEM = """Tu extrais des signaux physiologiques et de récupération d'un message d'athlète.
Règles : n'invente rien ; laisse null ce qui n'est pas dit. `illness_symptoms`
ne concerne QUE l'athlète (pas ses proches). Une nuit blanche ou un stress de
vie se code en sleep_quality / life_stress / fatigue. Alcool : `alcohol` =
moderate ou heavy (« beaucoup », « cuite », « gueule de bois » => heavy).
Heures de sommeil explicites => sleep_hours. Tension émotionnelle (dispute,
à bout, énervé) => life_stress et emotional_distress. `off_topic` = true si la
question posée ne porte pas sur l'entraînement ou la santé sportive."""

COACH_SYSTEM = """# Rôle
Tu es le coach d'endurance de l'athlète (trail / ultra). Tu es le seul à lui parler.
# Action
Proposer des modifications du plan des 7 prochains jours ET le message WhatsApp associé.
# Contexte
Tu reçois : état de charge (CTL/ATL/TSB/ACWR), plan courant, signaux du jour,
extraits scientifiques sourcés (evidence), et éventuellement des violations de
garde-fous d'une proposition précédente.
# Obligations
- Réutilise le session_id existant pour modifier une séance.
- Ne programme JAMAIS de séance de rattrapage après des séances manquées.
- Si des violations sont listées, corrige-les : elles ne sont pas négociables.
- Cite les evidence_ids utilisés. N'affirme rien de physiologique hors evidence.
- is_major_change=true si tu annules/ajoutes une séance clé ou touches > 2 jours.
- Réponds toujours à la question posée par l'athlète.
- Hors du champ sportif (couple, famille, travail) : une phrase d'empathie, aucun
  conseil hors de ta compétence (suggère d'en parler à un proche ou à un
  professionnel), puis ce que tu peux adapter côté entraînement.
- Récupération compromise (alcohol=heavy, sommeil < 5 h ou sleep_quality <= 2) :
  aucune séance intense dans les 36 h ; repos ou footing très facile, hydratation.
- Stress de vie >= 4/5 : propose d'alléger l'intensité du jour, sans l'imposer.
- Phase spécifique (si le profil l'indique) : relie la séance clé au profil de la course
  (montée, descente, allure, répétition générale) pour expliquer le pourquoi.
- Message : tutoiement, 4 phrases max, pas de jargon, ton adapté au profil.
- recent_chat = les derniers messages du fil (le dernier est celui de l'athlète) : réponds dans la continuité ;
  une réponse courte (« non », « ok ») répond à ta dernière question.
- Ne cite une séance (jour, type, durée) que si elle figure dans plan_next_7d. Les préférences du profil
  (« aime le double seuil », jours fixes…) ne sont pas des séances prévues. N'invente jamais un repos, une
  indisponibilité ou une sortie longue absents du plan.
- Charge : un ACWR bas (< 0,8) signifie une sous-charge, ce n'est jamais une raison de refuser une séance ;
  ce qui freine, c'est un ACWR haut (> 1,3), la fatigue, la douleur ou un garde-fou.
- Tu ne peux pas enregistrer une habitude permanente (jour du club, jour de la longue, jours de repos) : ne dis
  jamais « c'est noté pour les prochaines semaines » ; invite l'athlète à l'écrire simplement (« garde le mardi
  pour le club ») pour que le plan entier soit recalculé."""


# --------------------------------------------------------------------------- #
# Graphe
# --------------------------------------------------------------------------- #
def build_graph(deps: Deps, checkpointer=None):
    multi = agents_mode(deps) == "multi"

    def n_triage(s: CoachState):
        text = s["inbound"].text
        return {"route": deps.router(text), "emergency": is_emergency(text),
                "distress": is_distress(text)}

    def n_ack(s: CoachState):
        return {"reply": "👍", "final_changes": []}   # 0 token

    def n_emergency(s: CoachState):
        ctx = deps.repo.get(s["inbound"].athlete_id)
        cancels = [PlanChange(action="cancel", session=p, reason="emergency_symptoms")
                   for p in ctx.plan_next_7d if p.intensity != "rest"]
        return {"final_changes": cancels, "reply": (
            "Stop immédiat de l'entraînement. Ces symptômes nécessitent un avis médical "
            "rapide : appelle le 15 (ou le 112) s'ils sont présents maintenant. "
            "J'ai suspendu tes séances à venir.")}

    def n_distress(s: CoachState):
        # Humain d'abord : aucun appel LLM, aucune modification de plan.
        return {"final_changes": [], "reply": DISTRESS_REPLY}

    def n_extract(s: CoachState):
        msg = s["inbound"]
        sig = deps.llm.structured(node="extract", tier="small", system=EXTRACT_SYSTEM,
                                  user=f"Date: {deps.today()}\nMessage: {msg.text}",
                                  schema=Signals)
        return {"signals": sig}

    def n_evidence(s: CoachState):
        sig = s["signals"]
        tags = {"load"}
        if sig.unavailable_dates:
            tags |= {"missed_sessions", "detraining"}
        if sig.illness_symptoms:
            tags.add("illness")
        if sig.pain:
            tags.add("pain")
        from .knowledge import tags_for
        tags |= tags_for(s["inbound"].text)             # thèmes du message (seuil, ultra, volume...)
        ev = deps.retriever.search(s["inbound"].text, tags)
        return {"evidence": [{"id": e.id, "source": e.source, "text": e.text} for e in ev]}

    def n_coach(s: CoachState):
        ctx = deps.repo.get(s["inbound"].athlete_id)
        today = deps.today()
        payload = {
            "today": str(today),
            "days_to_race": (ctx.race_date - today).days if ctx.race_date else None,
            "profile": ctx.profile_summary,
            "load": {k: round(v, 2) for k, v in asdict(ctx.load).items() if v is not None},
            "plan_next_7d": [p.model_dump(mode="json") for p in ctx.plan_next_7d],
            "signals": s["signals"].model_dump(mode="json", exclude_none=True),
            "athlete_message": s["inbound"].text,
            "recent_chat": deps.history(),
            "evidence": s.get("evidence", []),
            "previous_violations": ([v.model_dump() for v in s["verdict"].blocking]
                                    if s.get("verdict") else []),
        }
        if s.get("verdict") and s["verdict"].blocking:   # v0.50.1 : dernier essai, réponds sans forcer le plan
            payload["instruction"] = ("Dernier essai : ta proposition précédente enfreignait les règles ci-dessus. "
                                      "Corrige-la, ou ne propose AUCUN changement (changes vide) et réponds "
                                      "simplement à l'athlète.")
        if multi:
            payload.update(team_payload(s))
        system = COACH_SYSTEM + (COACH_TEAM_ADDENDUM if multi else "") + deps.persona()
        decision = deps.llm.structured(node="coach", tier="main", system=system,
                                       user=json.dumps(payload, ensure_ascii=False),
                                       schema=CoachDecision)
        return {"decision": decision, "attempts": s.get("attempts", 0) + 1}

    def n_guard(s: CoachState):
        ctx = deps.repo.get(s["inbound"].athlete_id)
        plan_after = apply_changes(ctx.plan_next_7d, s["decision"].changes)
        verdict = check(plan_after, s["signals"], PlanContext(
            state=ctx.load, baseline_week_load=ctx.baseline_week_load, today=deps.today(),
            race_date=ctx.race_date, shock_block_active=ctx.shock_block_active,
            pre_taper_week_load=ctx.pre_taper_week_load,
            pre_taper_sessions_per_week=ctx.pre_taper_sessions_per_week,
            longest_run_30d_min=ctx.longest_run_30d_min, week_max=ctx.week_max), plan_before=ctx.plan_next_7d)
        if verdict.blocking:                 # v0.50.1 : déjà enfreint par le plan actuel -> ne bloque plus
            before = check(ctx.plan_next_7d, s["signals"], PlanContext(
                state=ctx.load, baseline_week_load=ctx.baseline_week_load, today=deps.today(),
                race_date=ctx.race_date, shock_block_active=ctx.shock_block_active,
                pre_taper_week_load=ctx.pre_taper_week_load,
                pre_taper_sessions_per_week=ctx.pre_taper_sessions_per_week,
                longest_run_30d_min=ctx.longest_run_30d_min, week_max=ctx.week_max))
            verdict = tolerate_preexisting(verdict, before, deps.tolerated())
        return {"verdict": verdict}

    def n_fallback(s: CoachState):
        ctx = deps.repo.get(s["inbound"].athlete_id)
        why = sorted({RULE_LABELS.get(v.rule_id, v.rule_id) for v in s["verdict"].blocking})
        changes = safe_fallback(ctx.plan_next_7d, s["signals"], deps.today())
        motif = f" ({', '.join(why)})" if why else ""
        if changes:                          # signal de risque : seules les séances intenses des 48 h sont allégées
            reply = (f"Je préfère jouer la prudence{motif} : j'allège tes séances intenses des deux prochains jours "
                     "en footing très facile. Dis-moi comment tu te sens après la prochaine sortie.")
        else:                                # v0.50.1 : le plan est gardé tel quel, rien n'est réécrit
            reply = (f"Je garde ton plan tel quel : le changement que j'envisageais ne passait pas mes garde-fous"
                     f"{motif}. Repose-moi ta question si je n'y ai pas répondu.")
        return {"final_changes": changes, "reply": reply}

    def n_confirm(s: CoachState):
        d = s["decision"]
        ctx = deps.repo.get(s["inbound"].athlete_id)
        changes, capped = cap_peak_sessions(d.changes, PlanContext(
            state=ctx.load, baseline_week_load=ctx.baseline_week_load, today=deps.today(),
            longest_run_30d_min=ctx.longest_run_30d_min))
        note = ("\n\n" + " ".join(capped)) if capped else ""
        if not d.is_major_change and not capped:   # changement mineur => mise à jour silencieuse
            return {"final_changes": d.changes, "reply": d.message_to_athlete}
        if not d.is_major_change:
            return {"final_changes": changes, "reply": d.message_to_athlete + note}
        question = d.message_to_athlete + note + "\n\nJe mets à jour ton agenda ? (oui / non)"
        verdict: Consent = "unclear"
        for _ in range(2):                  # 2 relances max, puis on garde le plan (prudence)
            verdict = deps.consent(str(interrupt({"type": "confirm", "message": question})))
            if verdict != "unclear":
                break
            question = "Je n'ai pas bien compris : je modifie ton plan, oui ou non ?"
        if verdict == "yes":
            return {"final_changes": changes, "reply": "C'est dans ton agenda ✅"}
        return {"final_changes": [], "reply": "OK, je garde le plan actuel."}

    def n_calendar(s: CoachState):
        ops = deps.calendar.apply(s["inbound"].athlete_id, s.get("final_changes", []))
        return {"calendar_ops": ops}

    # --- routage conditionnel ------------------------------------------------
    def after_triage(s: CoachState) -> str:
        if s["emergency"]:
            return "emergency"
        if s.get("distress"):
            return "distress"
        return "ack" if s["route"] == Route.NOISE else "extract"

    def after_guard(s: CoachState) -> str:
        if s["verdict"].allowed:
            return "confirm"
        return "coach" if s["attempts"] < MAX_COACH_ATTEMPTS else "fallback"

    g = StateGraph(CoachState)
    for name, fn in [("triage", n_triage), ("ack", n_ack), ("emergency", n_emergency),
                     ("distress", n_distress),
                     ("extract", n_extract), ("evidence", n_evidence), ("coach", n_coach),
                     ("guard", n_guard), ("fallback", n_fallback), ("confirm", n_confirm),
                     ("calendar", n_calendar)]:
        g.add_node(name, fn)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", after_triage, ["ack", "emergency", "distress", "extract"])
    g.add_edge("ack", END)
    g.add_edge("distress", END)
    g.add_edge("extract", "evidence")
    if multi:                                   # v0.40 : équipe d'agents entre evidence et guard
        t = make_team_nodes(deps, MAX_COACH_ATTEMPTS)
        for name in ("route", "specialist", "team", "critic"):
            g.add_node(name, t[name])
        g.add_edge("evidence", "route")
        g.add_conditional_edges("route", t["fan_out"], ["specialist", "team"])
        g.add_edge("specialist", "team")
        g.add_edge("team", "coach")
        g.add_conditional_edges("coach", t["after_coach"], ["critic", "guard"])
        g.add_conditional_edges("critic", t["after_critic"], ["coach", "guard"])
    else:
        g.add_edge("evidence", "coach")
        g.add_edge("coach", "guard")
    g.add_conditional_edges("guard", after_guard, ["confirm", "coach", "fallback"])
    for n in ("confirm", "fallback", "emergency"):
        g.add_edge(n, "calendar")
    g.add_edge("calendar", END)
    return g.compile(checkpointer=checkpointer)
