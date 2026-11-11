/* progrès (v0.19) : objectifs, profil, forme/fatigue, cumul 7 j, chronos, efficacité, semaines, activités */
import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { EstimatesNow, Point, Profile, ProgressView } from "../api/types";
import { LineChart } from "../components/Chart";
import { EyeCard } from "../components/EyeCard";
import { Metric } from "../components/Metric";
import { RadarChart } from "../components/RadarChart";
import { dayLabel, fmtDur, fmtPace, fr1, lsGet, lsSet, signed } from "../lib/format";
import { sortGoalCards } from "../lib/goals";
import { Activities } from "./Activities";

const RANGES: [string, number][] = [["3 mois", 91], ["6 mois", 182], ["1 an", 365], ["5 ans", 1826]];
const getRange = (): number => +(lsGet("progRange") || 0) || 182;
const RK: Record<string, [string, 1 | 2 | 3 | 4, (v: number) => string]> = {
  km: ["km", 1, (v) => `${fr1(v)} km`], h: ["heures", 2, (v) => `${fr1(v)} h`],
  dplus: ["D+", 3, (v) => `${Math.round(v)} m`], load: ["charge", 4, (v) => `${Math.round(v)}`],
};

function ProfileCard({ pr }: { pr: Profile | null }): ReactElement | null {
  if (!pr || !pr.axes) return null;
  const hasOld = pr.axes.some((a) => a.score_3m !== null);
  return (
    <div className="card">
      <h3>Ton profil de coureur</h3>
      <div className="muted small">{"Une note sur 10 par qualité, calculée sur tes données. 10 = repère d'un amateur expert." + (hasOld ? " En pointillés : il y a 3 mois." : "")}</div>
      <RadarChart axes={pr.axes} />
      {pr.weakest ? <div>{`Point fort : ${(pr.strongest || "").toLowerCase()} · à travailler en priorité : ${pr.weakest.toLowerCase()}.`}</div> : null}
      <details className="alt">
        <summary>Le détail de chaque note</summary>
        {pr.axes.map((a) => {
          const d = a.score !== null && a.score_3m !== null ? Math.round((a.score - a.score_3m) * 10) / 10 : null;
          return (
            <div key={a.key} className="axis">
              <div className="axis-head"><b>{a.label}</b>
                <span className="axis-score">{a.score === null ? "à mesurer" : `${fr1(a.score)}/10`}
                  {d !== null && Math.abs(d) >= 0.3 ? <span className={d > 0 ? "good" : "warnt"}>{` ${d > 0 ? "+" : "−"}${fr1(Math.abs(d))}`}</span> : null}</span></div>
              {a.value ? <div>{a.value}<span className="conf">{` · confiance ${a.confidence}`}</span></div> : null}
              {a.basis ? <div className="muted small">{a.basis}</div> : null}
              <div className="small tip">{a.tip}</div>
            </div>
          );
        })}
        <div className="axis"><b>Non mesurés sans laboratoire</b>
          {pr.not_measured.map((m) => <div key={m.key} className="small"><b>{m.label}</b>{` : ${m.why} ${m.how}`}</div>)}</div>
      </details>
    </div>
  );
}

function Chronos({ p, cur }: { p: ProgressView; cur: EstimatesNow }): ReactElement {
  return (
    <div className="chronos">
      {["5 km", "10 km", "semi", "marathon"].map((k) => {
        const pts: Point[] = p.estimates.map((e) => [e.day, (e[k] as number | undefined) ?? null]);
        const vals = pts.filter((q) => q[1] !== null);
        const old = vals.length > 4 ? vals[vals.length - 5][1] : null;
        const now = cur[k] as number;
        const dd = old ? now - old : null;
        return (
          <div key={k} className="chrono">
            <div className="k">{k}</div><div className="v">{fmtDur(now)}</div>
            {dd !== null && Math.abs(dd) >= 5 ? <div className={dd < 0 ? "good" : "warnt"}>{`${dd < 0 ? "−" : "+"}${fmtDur(Math.abs(dd))} / 4 sem.`}</div>
              : <div className="muted small">stable</div>}
            <LineChart series={[{ label: k, color: "accent", points: pts }]} height={90} yFmt={fmtDur} invert spark />
          </div>
        );
      })}
    </div>
  );
}

export function Progress({ active, nonce }: { active: boolean; nonce: number }): ReactElement {
  const [p, setP] = useState<ProgressView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [days, setDays] = useState(getRange);
  const [rollKey, setRollKey] = useState("km");
  const [loads, setLoads] = useState(0);          // chaque rendu complet relance la liste des activités
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<ProgressView>(`/api/progress?days=${days}`).then((r) => { if (!stale) { setErr(null); setP(r); setLoads((n) => n + 1); } })
      .catch((e: Error) => { if (!stale) { setErr(e.message); setP(null); } });
    return () => { stale = true; };
  }, [active, nonce, days]);

  let body: ReactElement;
  if (err) body = <div className="empty">{err}</div>;
  else if (!p) body = <div className="empty">Calcul de ta progression…</div>;
  else if (p.empty) body = <div className="empty">Aucune sortie pour l'instant : connecte Strava dans État.</div>;
  else {
    const C = { ctl: "accent", atl: "warn", tsb: "ink" } as const;
    const n = p.now;
    const markers = p.race_form ? [{ iso: p.race_form.day, label: "objectif" }] : [];
    const [rlbl, ri, rfmt] = RK[rollKey];
    const cur = p.estimates_now;
    const wk = [...p.weeks].reverse().slice(0, 8);
    body = <>
      <div className="seg">
        {RANGES.map(([l, d]) => <button key={d} className={"chip" + (d === days ? " on" : "")} onClick={() => { lsSet("progRange", String(d)); setDays(d); }}>{l}</button>)}
      </div>
      {sortGoalCards(p.goals || []).map((g) => {
        const key = `goal:${g.name}`;
        const pts: Point[] = p.estimates.map((e) => [e.day, e[key] ? (e[key] as number[])[1] : null]);
        const d = g.delta_28d;
        const trend = d === null || d === undefined ? "" : Math.abs(d) < 60 ? "stable sur 4 semaines" : `${d < 0 ? "−" : "+"}${fmtDur(Math.abs(d))} en 4 semaines`;
        return (
          <div key={g.name} className="card goal">
            <div className="card-head"><span className="eyebrow">{g.label || "Objectif"}</span>{g.days_to !== null ? <span className="muted">{`J-${g.days_to}`}</span> : null}</div>
            <h2>{g.name}</h2>
            <div className="muted">{[g.date ? dayLabel(g.date) : "", g.km ? `${g.km} km` : "", g.dplus ? `${g.dplus} m D+` : ""].filter(Boolean).join(" · ")}</div>
            {g.estimate ? <div className="goal-est"><span className="big">{`≈ ${fmtDur(g.estimate[1])}`}</span><span className="muted">{` entre ${fmtDur(g.estimate[0])} et ${fmtDur(g.estimate[2])}`}</span></div>
              : <div className="muted">Pas encore de référence de vitesse (un effort plat de 5 km ou plus).</div>}
            {trend ? <div className={d !== null && d < 0 ? "good" : d !== null && d > 60 ? "warnt" : "muted"}>{trend}</div> : null}
            <LineChart series={[{ label: "estimation", color: "accent", points: pts }]} height={120} yFmt={fmtDur} invert />
            <div className="chart-note">Estimation à partir de ta référence de vitesse et de ton endurance, pour un parcours roulant. Le terrain technique, la chaleur et la nuit ne sont pas pris en compte.</div>
            {(g.role || "A") === "A" && (g.days_to === null || g.days_to >= 0) ? <a className="btn small sim-link" href="#course">Simuler ma course</a> : null}
          </div>
        );
      })}
      {p.race_form ? (
        <div className="card">
          <div className="eyebrow">Forme prévue le jour J</div>
          <div><b>{`Fraîcheur ${signed(p.race_form.tsb)} (${p.race_form.label})`}</b>{` · forme ${fr1(p.race_form.ctl)}`}</div>
          <div className="muted">{`Le ${dayLabel(p.race_form.day)}, si tu suis le plan. Idéal le jour d'une course : entre +5 et +25.`}</div>
        </div>
      ) : null}
      {(p.goals || []).length ? <div className="muted small goal-hint">Pour changer un objectif, <a href="#coach">dis-le au coach</a>.</div> : null}
      {(p.goals_without_distance || []).length ? <div className="muted small">{`Pas de distance connue pour : ${(p.goals_without_distance || []).join(", ")}. Donne-la au coach pour avoir une estimation.`}</div> : null}
      <EyeCard active={active} />
      <ProfileCard pr={p.profile} />
      <div className="metrics">
        <Metric k="Forme (CTL)" v={fr1(n.ctl)} hint="charge moyenne 42 j" />
        <Metric k="Fatigue (ATL)" v={fr1(n.atl)} hint="charge moyenne 7 j" />
        <Metric k="Fraîcheur (TSB)" v={signed(n.tsb === null ? null : Math.round(n.tsb))} hint={n.tsb_label}
          cls={n.tsb !== null && n.tsb < -30 ? "bad" : n.tsb !== null && n.tsb < -10 ? "warn" : ""} />
        <Metric k="7 derniers jours" v={`${fr1(n.km7)} km`} hint={`${fr1(n.h7)} h · ${n.dplus7} m D+`} />
      </div>
      <div className="card"><h3>Forme et fatigue</h3>
        <div className="muted small">Forme : charge moyenne des 6 dernières semaines. Fatigue : des 7 derniers jours. En pointillés : ce que donne le plan s'il est suivi.</div>
        <LineChart markers={markers} series={[
          { label: "Forme", color: C.ctl, points: p.pmc.map((r) => [r[0], r[1]]) },
          { label: "Fatigue", color: C.atl, points: p.pmc.map((r) => [r[0], r[2]]) },
          { label: "Forme prévue", color: C.ctl, dashed: true, points: p.projection.map((r) => [r[0], r[1]]) },
          { label: "Fatigue prévue", color: C.atl, dashed: true, hideLegend: true, points: p.projection.map((r) => [r[0], r[2]]) }]} />
      </div>
      <div className="card"><h3>Fraîcheur</h3>
        <div className="muted small">Forme moins fatigue. Négative quand tu charges, positive quand tu récupères ou t'affûtes.</div>
        <LineChart height={130} zero markers={markers} yFmt={(v) => (v > 0 ? "+" : "") + Math.round(v)} series={[
          { label: "Fraîcheur", color: C.tsb, points: p.pmc.map((r) => [r[0], r[3]]) },
          { label: "prévue", color: C.tsb, dashed: true, points: p.projection.map((r) => [r[0], r[3]]) }]} />
      </div>
      <div className="card"><h3>Cumul sur 7 jours glissants</h3>
        <div>
          <div className="seg">
            {Object.entries(RK).map(([k, v]) => <button key={k} className={"chip" + (k === rollKey ? " on" : "")} onClick={() => setRollKey(k)}>{v[0]}</button>)}
          </div>
          <LineChart key={rollKey} series={[{ label: rlbl, color: "accent", area: true, points: p.rolling7.map((r) => [r[0], r[ri]]) }]} yFmt={rfmt} />
        </div>
      </div>
      <div className="card"><h3>Chronos estimés</h3>
        {cur ? <Chronos p={p} cur={cur} /> : <div className="empty">Pas encore de référence de vitesse : un effort plat de 5 km ou plus suffit.</div>}
        {cur ? <div className="chart-note">{`D'après ta référence : ${cur.ref.name} (${dayLabel(cur.ref.day)}), `
          + `meilleur effort plat des 9 derniers mois. Marathon corrigé de ton endurance (+${Math.round(cur.marathon_gap * 100)} %).`}</div> : null}
      </div>
      {p.efficiency.length >= 3 ? (
        <div className="card"><h3>Efficacité en endurance</h3>
          <div className="muted small">{`Allure de tes footings plats ramenée à ${p.efficiency_hr} bpm. Plus elle est rapide, plus ton endurance progresse.`}</div>
          <LineChart series={[{ label: `allure à ${p.efficiency_hr} bpm`, color: "accent", points: p.efficiency.map((r) => [r[0], r[1]]) }]} yFmt={fmtPace} invert height={150} />
        </div>
      ) : null}
      <div className="card"><h3>Semaines</h3>
        <div className="table-wrap"><table className="weeks">
          <thead><tr>{["Sem.", "km", "h", "D+", "charge", "fait", "mono."].map((t) => <th key={t}>{t}</th>)}</tr></thead>
          <tbody>{wk.map((w) => (
            <tr key={w.start} className={w.partial ? "partial" : ""}>
              <td>{`${dayLabel(w.start).replace(/^\S+ /, "")}${w.partial ? " *" : ""}`}</td><td>{w.km}</td><td>{fr1(w.hours)}</td>
              <td>{w.dplus}</td><td>{w.load}</td><td>{w.planned ? `${w.done}/${w.planned}` : "—"}</td>
              <td className={w.monotony !== null && w.monotony >= 2 ? "warnt" : ""}>{w.monotony === null ? "—" : fr1(w.monotony)}</td>
            </tr>
          ))}</tbody>
        </table></div>
        <div className="chart-note">* semaine en cours. Fait : jours prévus où tu as bien couru. Monotonie (Foster) : au-delà de 2, les journées se ressemblent trop ; alterner facile et dur limite le risque de surmenage.</div>
      </div>
      <div className="card"><div className="card-head"><h3>Activités</h3></div><Activities key={loads} /></div>
    </>;
  }
  return <section id="view-progres" className="view" data-view="progres" hidden={!active}>{body}</section>;
}
