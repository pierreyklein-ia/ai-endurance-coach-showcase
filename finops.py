"""Modèle FinOps par athlète et par mois — toutes les hypothèses sont explicites.

Prix vérifiés en sept. 2026 (à re-vérifier avant soutenance) :
- Claude Haiku 4.5 : $1 / $5 par MTok (cache read $0.10) ; Sonnet 5 : $2 / $10
  (cache read $0.20, cache write 5 min $2.50) ; Batch API : -50 %.
- Tokenizer des modèles récents : ~+30 % de tokens (appliqué au tier "main").
- STT Voxtral Mini Transcribe V2 (Mistral, UE) : $0.003 / min.
- WhatsApp FR : messages dans la fenêtre de 24 h gratuits ; template "utility"
  hors fenêtre ≈ $0.030 (grille indicative, à confirmer sur le rate card Meta).
Hypothèses NON vérifiées (paramètres) : change, frais PSP, infra fixe.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

USD_EUR = 0.86                 # hypothèse de change
PRICE_TTC = 15.0
VAT = 0.20
PSP_PCT, PSP_FIXED = 0.015, 0.25   # hypothèse frais de paiement carte UE
INFRA_FIXED_EUR = 60.0         # hypothèse : conteneurs + Postgres/pgvector + monitoring

PRICES = {  # USD / MTok : (input, output, cache_read, cache_write)
    "haiku": (1.0, 5.0, 0.10, 1.25),
    "sonnet": (2.0, 10.0, 0.20, 2.50),
    "jev": (0.042, 0.0, 0.042, 0.042),   # TypeSafe JEV, early access (sortie annoncée gratuite)
}


@dataclass
class Scenario:
    name: str
    msgs: float = 30                 # messages entrants / mois
    noise_share: float = 0.35        # "ok", "👍", "merci"
    voice_share: float = 0.5
    voice_min: float = 0.75
    triage_llm: bool = False         # triage fait par le gros modèle ?
    extract_model: str = "haiku"
    extract_in: int = 900
    extract_out: int = 120
    coach_model: str = "sonnet"
    coach_calls_per_exchange: float = 1.15   # 15 % de retry après garde-fou
    coach_static_in: int = 3000      # prompt système + schéma outil (cacheable)
    coach_dynamic_in: int = 4000     # profil résumé, charge, plan, signaux, evidence
    coach_out: int = 600
    cache_hit: float = 0.9           # part du préfixe statique lue depuis le cache
    tokenizer_factor: float = 1.3
    weekly_plans: float = 4.3
    plan_in: int = 9000
    plan_out: int = 2500
    plan_batch: bool = True          # replanif hebdo via Batch API (-50 %)
    proactive_msgs: float = 12
    proactive_outside_window: float = 0.6
    wa_template_usd: float = 0.030


def _cost(model, tin, tout, cached=0, batch=False, factor=1.0):
    pin, pout, pcr, _ = PRICES[model]
    tin, tout, cached = tin * factor, tout * factor, cached * factor
    usd = ((tin - cached) * pin + cached * pcr + tout * pout) / 1e6
    return usd * (0.5 if batch else 1.0)


def monthly_variable_eur(s: Scenario) -> dict[str, float]:
    exchanges = s.msgs * (1 - s.noise_share)
    f = s.tokenizer_factor
    llm = 0.0
    if s.triage_llm:   # anti-pattern : payer le gros modèle pour lire "ok merci"
        llm += s.msgs * _cost(s.coach_model, 800, 50, factor=f)
    llm += exchanges * _cost(s.extract_model, s.extract_in, s.extract_out)
    coach_in = s.coach_static_in + s.coach_dynamic_in
    llm += exchanges * s.coach_calls_per_exchange * _cost(
        s.coach_model, coach_in, s.coach_out, cached=s.coach_static_in * s.cache_hit, factor=f)
    llm += s.weekly_plans * _cost(s.coach_model, s.plan_in, s.plan_out, batch=s.plan_batch, factor=f)
    stt = s.msgs * s.voice_share * s.voice_min * 0.003
    wa = s.proactive_msgs * s.proactive_outside_window * s.wa_template_usd
    return {k: v * USD_EUR for k, v in {"llm": llm, "stt": stt, "whatsapp": wa}.items()}


def unit_economics(s: Scenario, n_athletes: int) -> dict[str, float]:
    var = monthly_variable_eur(s)
    ht = PRICE_TTC / (1 + VAT)
    psp = PRICE_TTC * PSP_PCT + PSP_FIXED
    infra = INFRA_FIXED_EUR / n_athletes
    cost = sum(var.values()) + psp + infra
    return {**var, "psp": psp, "infra": infra, "total_cost": cost,
            "revenue_ht": ht, "gross_margin": (ht - cost) / ht}


OPTIMISED = Scenario("Archi proposée (triage règles + Haiku extract + Sonnet 1 appel)")
NAIVE = replace(OPTIMISED, name="Naïf (tout au gros modèle, historique complet, sans cache)",
                noise_share=0.0, triage_llm=True, extract_model="sonnet",
                coach_calls_per_exchange=2.3, coach_dynamic_in=12000, cache_hit=0.0,
                plan_batch=False)

WITH_JEV = replace(OPTIMISED, name="Archi proposée + JEV (triage, consentement, extraction)",
                   extract_model="jev", noise_share=0.40)


# --------------------------------------------------------------------------- v0.40 : équipe d'agents
@dataclass
class TeamBudget:
    """Budget de tokens d'UN message de coaching (hors bruit), mode `single` ou `multi` (hypothèses explicites).

    multi : superviseur Haiku (40 % des messages tranchés par règle, 0 token), 1,4 spécialiste Haiku par message
    (mesuré par eval/agents_eval.py), 2,2 allers-retours d'outils chacun (prompt + définitions d'outils en cache),
    critique Haiku (1,1 passage), coach Sonnet avec ~1 500 tokens de notes d'équipe en plus, moins de relances
    des garde-fous (le critique corrige avant)."""
    multi: bool = True
    supervisor_share: float = 0.6
    supervisor_in: int = 1100
    supervisor_out: int = 60
    specialists: float = 1.4
    steps: float = 2.2
    spec_static_in: int = 2100        # rôle + 6 définitions d'outils + schéma de réponse (cacheables)
    spec_dynamic_in: int = 1200       # message, signaux, résultats d'outils accumulés
    spec_out_per_step: int = 140
    critic_rounds: float = 1.1
    critic_in: int = 3200
    critic_out: int = 150
    team_notes_in: int = 1500
    coach_calls: float = 1.1          # single : 1,15 (relances garde-fous) ; multi : critique (8 %) + garde-fous


def per_message_eur(b: TeamBudget, s: Scenario = None) -> dict[str, float]:
    s = s or Scenario("base")
    f = s.tokenizer_factor
    out = {"extraction": _cost(s.extract_model, s.extract_in, s.extract_out)}
    coach_in = s.coach_static_in + s.coach_dynamic_in + (b.team_notes_in if b.multi else 0)
    calls = b.coach_calls if b.multi else s.coach_calls_per_exchange
    out["coach"] = calls * _cost(s.coach_model, coach_in, s.coach_out, cached=s.coach_static_in * s.cache_hit, factor=f)
    if b.multi:
        out["superviseur"] = b.supervisor_share * _cost("haiku", b.supervisor_in, b.supervisor_out)
        out["specialistes"] = b.specialists * b.steps * _cost(
            "haiku", b.spec_static_in + b.spec_dynamic_in, b.spec_out_per_step, cached=b.spec_static_in * s.cache_hit)
        out["critique"] = b.critic_rounds * _cost("haiku", b.critic_in, b.critic_out)
    out = {k: v * USD_EUR for k, v in out.items()}
    out["total"] = sum(out.values())
    return out


def eye_eur(cache_hit: float = 0.9) -> dict[str, float]:
    """« Œil du coach » (1 analyse / 30 jours) : 4 spécialistes × 3 allers-retours (fenêtres de 8 semaines),
    rapport sur Sonnet (~4 000 tokens de notes + fiches, ~900 en sortie), critique Haiku (1,2 passage)."""
    spec = 4 * 3 * _cost("haiku", 2100 + 2400, 160, cached=2100 * cache_hit)
    report = 1.2 * _cost("sonnet", 1200 + 4000, 900, cached=1200 * cache_hit)
    critic = 1.2 * _cost("haiku", 2600, 150)
    out = {k: v * USD_EUR for k, v in {"specialistes": spec, "rapport": report, "critique": critic}.items()}
    out["total"] = sum(out.values())
    return out


if __name__ == "__main__":
    ht = PRICE_TTC / (1 + VAT)
    print(f"Spec v1 : coût 2,35 € ; marge annoncée sur TTC = {(PRICE_TTC-2.35)/PRICE_TTC:.0%}"
          f" ; recalculée sur HT et frais PSP = "
          f"{(ht - 2.35 - (PRICE_TTC*PSP_PCT+PSP_FIXED))/ht:.0%}\n")
    for sc in (OPTIMISED, WITH_JEV, NAIVE):
        print(sc.name)
        for n in (50, 500, 5000):
            u = unit_economics(sc, n)
            print(f"  N={n:>5} | LLM {u['llm']:.2f} € | STT {u['stt']:.2f} € | WA {u['whatsapp']:.2f} € "
                  f"| PSP {u['psp']:.2f} € | infra {u['infra']:.2f} € | total {u['total_cost']:.2f} € "
                  f"| marge brute {u['gross_margin']:.0%}")
        print()
    for name, b in (("single (v0.39)", TeamBudget(multi=False)), ("multi (v0.40)", TeamBudget())):
        c = per_message_eur(b)
        print(f"Message de coaching, {name} : " + " | ".join(f"{k} {v * 100:.2f} c€" for k, v in c.items()))
    e = eye_eur()
    print("Œil du coach (1 analyse) : " + " | ".join(f"{k} {v * 100:.2f} c€" for k, v in e.items()))
