/* v0.40 : l'équipe d'agents derrière une réponse du coach (avatars, outils, veto, critique). */
import type { ReactElement } from "react";
import type { AgentTrace, ChatMeta, CriticTrace } from "../api/types";

export const AGENTS: Record<string, { label: string; icon: string; short: string }> = {
  prevention: { label: "Prévention & santé", icon: "🩺", short: "Prévention" },
  charge: { label: "Charge & planification", icon: "📈", short: "Charge" },
  nutrition: { label: "Nutrition", icon: "🍌", short: "Nutrition" },
  course: { label: "Stratégie de course", icon: "🏁", short: "Course" },
};
const RISK: Record<string, string> = { low: "risque faible", medium: "vigilance", high: "risque élevé" };

export function criticLabel(c?: CriticTrace | null): string {
  if (!c) return "";
  if (c.verdict === "ok") return c.revised ? "validé après 1 révision" : "validé";
  return c.revised ? "réserves transmises aux garde-fous" : "à revoir";
}

/** « Équipe consultée : Nutrition (2 outils) · Prévention ⛔ veto · Critique : validé après 1 révision » */
export function teamLine(meta: ChatMeta): string {
  const ag = meta.agents || [];
  const parts = ag.map((a) => `${AGENTS[a.name]?.short || a.label} (${a.tools.length} outil${a.tools.length > 1 ? "s" : ""})${a.veto ? " ⛔ veto" : ""}`);
  return `Équipe consultée : ${parts.length ? parts.join(" · ") : "coach seul"}${meta.critic ? ` · Critique : ${criticLabel(meta.critic)}` : ""}`;
}

const cents = (eur: number) => `${(eur * 100).toFixed(2).replace(".", ",")} c€`;
const sec = (ms: number) => `${(ms / 1000).toFixed(1).replace(".", ",")} s`;

/** Puce compacte d'un agent : « 🩺 Prévention · 3 outils · 0,12 c€ · 0,9 s » */
export function agentChip(a: AgentTrace): string {
  return `${AGENTS[a.name]?.icon || a.icon} ${AGENTS[a.name]?.short || a.label} · ${a.tools.length} outil${a.tools.length > 1 ? "s" : ""} · ${cents(a.cost_eur)} · ${sec(a.ms)}`;
}

/** Puces de coût : total du message (meta.cost_eur, latency_s) et part du coach / du critique (cost_by_agent). */
export function costChips(meta: ChatMeta): string[] {
  const out: string[] = [];
  const by = meta.cost_by_agent || {};
  if (by.coach !== undefined) out.push(`🏃 Coach · ${cents(by.coach)}`);
  if (by.critique !== undefined) out.push(`🧐 Critique · ${cents(by.critique)}`);
  if (meta.cost_eur !== undefined || meta.latency_s !== undefined) {
    out.push(`Total · ${cents(meta.cost_eur ?? 0)}${meta.latency_s !== undefined ? ` · ${String(meta.latency_s).replace(".", ",")} s` : ""}`);
  }
  return out;
}

export function AgentAvatar({ name, size = "sm" }: { name: string; size?: "sm" | "md" }): ReactElement {
  const a = AGENTS[name];
  return <span className={`agent-av agent-${name} ${size}`} title={a?.label} aria-hidden="true">{a?.icon || "•"}</span>;
}

/** Résumé visible sous la bulle : mini-avatars + verdict du critique (le détail s'ouvre au toucher). */
export function TeamStrip({ meta }: { meta: ChatMeta }): ReactElement {
  const ag = meta.agents || [];
  const c = meta.critic;
  return (
    <span className="team-strip">
      {ag.length ? ag.map((a) => <AgentAvatar key={a.name} name={a.name} />) : <span className="agent-av coach sm" aria-hidden="true">💬</span>}
      {c ? <span className={"critic-chip" + (c.verdict === "ok" ? " ok" : " warn")}>{c.verdict === "ok" ? (c.revised ? "✓ révisé" : "✓") : "!"}</span> : null}
      <span className="sr">{teamLine(meta)}</span>
    </span>
  );
}

function AgentRow({ a }: { a: AgentTrace }): ReactElement {
  return (
    <li className={`agent-row agent-${a.name}`}>
      <AgentAvatar name={a.name} size="md" />
      <div className="agent-body">
        <div className="agent-head">
          <b>{AGENTS[a.name]?.label || a.label}</b>
          <span className={`risk-badge ${a.risk_level}`}>{RISK[a.risk_level]}</span>
          {a.veto ? <span className="veto-badge">⛔ veto</span> : null}
          {a.degraded ? <span className="risk-badge medium">hors ligne</span> : null}
        </div>
        {a.reason ? <div className="agent-why">{a.reason}</div> : null}
        {a.veto && a.veto_reason ? <div className="agent-why">{`Veto : ${a.veto_reason}`}</div> : null}
        <div className="agent-tools">
          {a.tools.map((t, i) => <span key={i} className="tool-chip">{t.name}<i>{` ${Math.round(t.ms)} ms`}</i></span>)}
        </div>
        {(a.risks || []).length ? <ul className="agent-risks">{(a.risks || []).map((r, i) => <li key={i}>{r}</li>)}</ul> : null}
        <div className="agent-foot">{`${a.evidence_ids.length} fiche${a.evidence_ids.length > 1 ? "s" : ""} citée${a.evidence_ids.length > 1 ? "s" : ""} · ${cents(a.cost_eur)} · ${sec(a.ms)}`}</div>
      </div>
    </li>
  );
}

/** Détail complet (dans « détails » d'un message, et dans le rapport de l'Œil du coach). */
export function TeamTrace({ meta }: { meta: ChatMeta }): ReactElement {
  const ag = meta.agents || [];
  return (
    <div className="team-trace">
      <div className="team-line">{teamLine(meta)}</div>
      <div className="team-chips">
        {ag.map((a) => <span key={a.name} className={`team-chip agent-${a.name}`}>{agentChip(a)}</span>)}
        {meta.critic ? <span className={"team-chip critic" + (meta.critic.verdict === "ok" ? "" : " warn")}>{`🧐 ${criticLabel(meta.critic)}`}</span> : null}
        {costChips(meta).map((c) => <span key={c} className="team-chip cost">{c}</span>)}
      </div>
      {meta.routing?.reason ? <div className="team-why">{`Superviseur (${meta.routing.by}) : ${meta.routing.reason}`}</div> : null}
      {ag.length ? <ul className="agent-list">{ag.map((a) => <AgentRow key={a.name} a={a} />)}</ul> : null}
      {meta.critic ? (
        <div className={"critic-row" + (meta.critic.verdict === "ok" ? " ok" : " warn")}>
          <span className="agent-av critic md" aria-hidden="true">🧐</span>
          <div><b>{`Critique : ${criticLabel(meta.critic)}`}</b>
            {meta.critic.reasons?.length ? <ul className="critic-reasons">{meta.critic.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : null}
          </div>
        </div>
      ) : null}
      <div className="team-guard">🛡 Garde-fous déterministes : dernier mot sur le plan</div>
    </div>
  );
}
