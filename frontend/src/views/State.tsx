import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { Memory, NutritionSummary, StateView } from "../api/types";
import { ActRow } from "../components/ActRow";
import { GoalsCard } from "../components/GoalsCard";
import { Metric } from "../components/Metric";
import { comma, dayLabel, signed } from "../lib/format";
import { fmtHours } from "../lib/nutrition";
import { toast } from "../lib/toast";

function MemoryCard({ m }: { m: Memory | null | undefined }): ReactElement | null {
  // v0.17 : ce que tu m'as dit et le plan validé, enregistrés et donnés au coach à chaque message
  if (!m || !m.commitments || !m.commitments.length) return null;
  const a = m.agreed_plan;
  return (
    <div className="card">
      <div className="eyebrow">Ce que tu m'as dit</div>
      <ul className="memory">{m.commitments.map((c, i) => <li key={i}>{c}</li>)}</ul>
      {a ? (
        <div className="muted">{`Plan validé le ${a.adopted_at.slice(8, 10)}/${a.adopted_at.slice(5, 7)} à ${a.adopted_at.slice(11, 16)} : `
          + `${a.sessions} séances du ${a.first_day.slice(8, 10)}/${a.first_day.slice(5, 7)} au ${a.last_day.slice(8, 10)}/${a.last_day.slice(5, 7)}`
          + (m.versions > 1 ? ` (version ${m.versions})` : "")}</div>
      ) : (
        <div className="muted">{m.has_plan ? "Plan validé avant la v0.17 : il est dans Semaine. Son texte sera enregistré à ta prochaine validation." : "Plan pas encore validé."}</div>
      )}
      {a && a.plan_text ? <details className="alt"><summary>Revoir le plan validé</summary><pre className="plan-text">{a.plan_text}</pre></details> : null}
      <div className="muted">Enregistré sur le serveur et sauvegardé chaque nuit. Pour changer quelque chose, dis-le au coach dans le chat.</div>
    </div>
  );
}

const DIG = ["", "très bien", "bien", "moyen", "mal", "très mal"];

function NutritionCard({ n }: { n: NutritionSummary | undefined }): ReactElement {
  // v0.38 : ce que les bilans des sorties longues disent de ta nutrition à l'effort
  const name = (i: string) => n?.labels?.[i] || i;
  return (
    <div className="card nutri-card" id="nutri-card">
      <div className="eyebrow">Nutrition à l'effort</div>
      {!n || !n.count ? <div className="muted">Après une sortie d'1 h 30 ou plus, valide la séance et note ce que tu as mangé : le coach t'aide à trouver ce qui te réussit.</div> : <>
        <div className="nutri-sum">
          <div><b>{n.trained_max_g_h ? `${Math.round(n.trained_max_g_h)} g/h` : "—"}</b><small>max toléré</small></div>
          {n.target_long_g_h ? <div><b>{`${n.target_long_g_h[0]}-${n.target_long_g_h[1]}`}</b><small>g/h visés (sortie de 3 h)</small></div> : null}
          <div><b>{n.count}</b><small>{n.count > 1 ? "bilans" : "bilan"}</small></div>
        </div>
        <ul className="nutri-last">
          {n.last.map((x, i) => (
            <li key={i}>
              <span className="t">{`${dayLabel(x.date)} · ${fmtHours(x.duration_h)}`}</span>
              <span className="g">{`${Math.round(x.carbs_g_h)} g/h`}</span>
              <span className={`dig d${x.digestion}`}>{`digestion ${x.digestion}/5 · ${DIG[x.digestion] || ""}`}</span>
              {x.symptoms.length ? <span className="muted small sy">{x.symptoms.map((s) => n.symptom_labels?.[s] || s).join(", ")}</span> : null}
            </li>
          ))}
        </ul>
        {n.tolerated.length ? <div className="nutri-foods ok"><span className="label">Ça passe bien</span>{n.tolerated.slice(0, 8).map((i) => <span key={i} className="chip">{name(i)}</span>)}</div> : null}
        {n.avoid.length ? <div className="nutri-foods ko"><span className="label">À éviter / à retester</span>{n.avoid.slice(0, 6).map((i) => <span key={i} className="chip">{name(i)}</span>)}</div> : null}
      </>}
    </div>
  );
}

export function State({ active, nonce }: { active: boolean; nonce: number }): ReactElement {
  const [s, setS] = useState<StateView | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<StateView>("/api/state").then((r) => { if (!stale) setS(r); }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce, reload]);

  const syncNow = async () => {
    toast("Synchronisation…", 8000);
    try { const r = await api<{ message: string }>("/api/sync", { method: "POST" }); toast(r.message); setReload((n) => n + 1); }
    catch (e) { toast((e as Error).message); }
  };

  return (
    <section id="view-etat" className="view" data-view="etat" hidden={!active}>
      {s ? <>
        <GoalsCard goals={s.goals} />
        <div className="metrics">
          <Metric k="Forme (CTL)" v={s.ctl ?? "n/d"} hint="charge chronique 42 j" />
          <Metric k="Fatigue (ATL)" v={s.atl ?? "n/d"} hint="charge aiguë 7 j" />
          <Metric k="Fraîcheur (TSB)" v={signed(s.tsb)} hint="forme − fatigue" />
          <Metric k="ACWR" v={s.acwr === null ? "n/d" : comma(s.acwr)}
            hint={s.history_days < 28 ? "historique < 28 j : peu fiable" : "alerte 1,5 · blocage 1,7"}
            cls={s.acwr !== null && s.acwr > 1.7 ? "bad" : s.acwr !== null && s.acwr > 1.5 ? "warn" : ""} />
        </div>
        <MemoryCard m={s.memory} />
        <NutritionCard n={s.nutrition} />
        <div className="card">
          <div className="card-head"><span className="eyebrow">Sorties (14 jours)</span><button className="btn small" onClick={syncNow}>Synchroniser</button></div>
          {s.last_sync ? <div className="muted">{`Dernière synchro Strava : ${s.last_sync.replace("T", " ").slice(0, 16)}`}</div> : null}
          {s.activities.length ? s.activities.map((a, i) => <ActRow key={i} a={a} />)
            : <div className="empty">Aucune sortie. Synchronise Strava ou importe un FIT depuis le chat.</div>}
        </div>
      </> : null}
    </section>
  );
}
