/* v0.40 : « Œil du coach » (Progrès) — analyse approfondie par toute l'équipe d'agents, débloquée au niveau 7. */
import { useCallback, useEffect, useState, type ReactElement, type ReactNode } from "react";
import { api } from "../api/client";
import type { EyeStatus } from "../api/types";
import { dayLabel, frInt } from "../lib/format";
import { toast } from "../lib/toast";
import { AGENTS, AgentAvatar, criticLabel, TeamTrace } from "./TeamTrace";

const TEAM_ORDER = ["prevention", "charge", "nutrition", "course"];

/** Section repliable du rapport (fermée par défaut, sauf les priorités). */
function Sec({ title, cls = "", open = false, children }: { title: string; cls?: string; open?: boolean; children: ReactNode }): ReactElement {
  return (
    <details className={`eye-sec ${cls}`} open={open}>
      <summary><h4>{title}</h4></summary>
      {children}
    </details>
  );
}

function List({ title, items, cls }: { title: string; items: string[]; cls: string }): ReactElement | null {
  if (!items.length) return null;
  return <Sec title={`${title} (${items.length})`} cls={cls}><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></Sec>;
}

export function EyeCard({ active }: { active: boolean }): ReactElement | null {
  const [st, setSt] = useState<EyeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<EyeStatus>("/api/eye").then(setSt).catch(() => {}), []);
  useEffect(() => { if (active) void load(); }, [active, load]);
  // analyse en cours (tâche de fond) : on relit toutes les 3 s
  useEffect(() => {
    if (!active || !st?.running) return;
    const id = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(id);
  }, [active, st?.running, load]);

  if (!st) return null;
  const start = async () => {
    setBusy(true);
    try {
      const r = await api<EyeStatus>("/api/eye", { method: "POST" });
      toast("L'équipe analyse tes 8 dernières semaines…");
      setSt((cur) => ({ ...(cur as EyeStatus), ...r, running: r.running || !!r.typing }));
      void load();
    } catch (e) { toast((e as Error).message); void load(); }
    finally { setBusy(false); }
  };
  const last = st.last;
  const rep = last?.report;

  const report = rep ? (
        <div className="eye-report">
          <p className="eye-summary">{rep.summary}</p>
          <Sec title="Tes 3 priorités" cls="eye-prios" open>
            <ol>{rep.priorities.map((p, i) => (
              <li key={i}><b>{p.action}</b>
                {p.why ? <div className="muted small">{p.why}</div> : null}
                {p.cards.length ? <div className="card-chips">{p.cards.map((c) => <span key={c} className="tool-chip">{c.replace(/^kb_/, "")}</span>)}</div> : null}
              </li>
            ))}</ol>
          </Sec>
          <div className="eye-grid">
            <List title="Points forts" items={rep.strengths} cls="good" />
            <List title="À travailler" items={rep.weaknesses} cls="warn" />
            <List title="Risques" items={rep.risks} cls="bad" />
          </div>
          {rep.nutrition ? <Sec title={`${AGENTS.nutrition.icon} Nutrition`}><p>{rep.nutrition}</p></Sec> : null}
          {rep.race ? <Sec title={`${AGENTS.course.icon} Course`}><p>{rep.race}</p></Sec> : null}
          {(rep.to_measure || []).length ? <List title="À mesurer d'ici le prochain point" items={rep.to_measure || []} cls="measure" /> : null}
          {rep.next_review ? <div className="muted small">{`Prochain point : ${rep.next_review}`}</div> : null}
          {(st.history || []).length > 1 ? (
            <Sec title={`Analyses précédentes (${(st.history || []).length - 1})`} cls="eye-history">
              <ul>{(st.history || []).slice(0, -1).reverse().map((h) => <li key={h.day}><b>{dayLabel(h.day)}</b> · {h.summary}</li>)}</ul>
            </Sec>
          ) : null}
          <details className="alt">
            <summary>{`Qui a contribué · critique : ${criticLabel(last?.critic)}`}</summary>
            <TeamTrace meta={{ agents: last?.agents, critic: last?.critic, routing: { specialists: TEAM_ORDER, reason: "toute l'équipe (Œil du coach)", by: "règle" } }} />
            <div className="muted small">{`${(last?.cost_eur ?? 0).toFixed(3).replace(".", ",")} € · ${(last?.latency_s ?? 0).toFixed(1).replace(".", ",")} s`}</div>
          </details>
        </div>
  ) : null;
  const gift = !st.unlocked && (st.free_available || st.running);   // v0.41 : première analyse offerte
  if (!st.unlocked && !gift) {
    return (
      <div className="card eye-card locked" id="oeil">
        <div className="card-head"><span className="eyebrow">👁 Œil du coach</span><span className="muted small">{`niveau ${st.level}`}</span></div>
        <h3>{`🔒 Niveau ${st.required_level} : l'Œil du coach analyse tout ton entraînement avec toute l'équipe d'agents`}</h3>
        <div className="eye-team" aria-hidden="true">{TEAM_ORDER.map((k) => <AgentAvatar key={k} name={k} size="md" />)}<span className="agent-av critic md">🧐</span></div>
        <div className="xpbar" role="progressbar" aria-valuenow={st.progress_pct} aria-valuemin={0} aria-valuemax={100} aria-label="Progression vers le niveau 7">
          <i style={{ width: `${Math.max(2, st.progress_pct)}%` }}></i>
        </div>
        <div className="muted small">{`${frInt(st.xp)} / ${frInt(st.xp_required)} XP (${st.progress_pct} %) : chaque séance validée te rapproche du niveau ${st.required_level}.`}</div>
        <div className="muted small">Prévention, charge, nutrition et stratégie de course relisent tes 8 dernières semaines ; le coach en tire 3 priorités, un critique vérifie. Une analyse par mois.</div>
        <a className="btn small" href="#carriere">Voir ma carrière</a>
        {report ? <><h4 className="eye-free-done">{last?.free ? "Ton analyse offerte" : "Ta dernière analyse"}</h4>{report}</> : null}
      </div>
    );
  }
  return (
    <div className="card eye-card" id="oeil">
      <div className="card-head"><span className="eyebrow">👁 Œil du coach</span>{last ? <span className="muted small">{`analyse du ${dayLabel(last.day)}`}</span> : null}</div>
      {gift ? <div className="eye-gift" role="note"><b>🎁 Ta première analyse est offerte.</b>{` Ensuite, l'Œil du coach se débloque au niveau ${st.required_level} (tu es au niveau ${st.level}).`}</div> : null}
      <h3>Toute l'équipe analyse tes 8 dernières semaines</h3>
      <div className="eye-team" aria-hidden="true">{TEAM_ORDER.map((k) => <AgentAvatar key={k} name={k} size="md" />)}<span className="agent-av critic md">🧐</span></div>
      <div className="row-actions">
        <button className="btn primary" disabled={!st.available || busy} onClick={start}>
          {st.running ? "Analyse en cours…" : gift ? "Lancer mon analyse offerte" : "Lancer l'analyse"}
        </button>
      </div>
      <div className="muted small">{st.running ? "Le coach écrit : le rapport arrive dans une minute, avec un message dans le chat."
        : st.next_at ? `Prochaine analyse possible le ${dayLabel(st.next_at)} (une tous les ${st.cooldown_days} jours).`
        : gift ? "Prévention, charge, nutrition et stratégie de course relisent tes 8 dernières semaines ; un critique vérifie." : `Une analyse tous les ${st.cooldown_days} jours.`}</div>
      {report}
    </div>
  );
}
