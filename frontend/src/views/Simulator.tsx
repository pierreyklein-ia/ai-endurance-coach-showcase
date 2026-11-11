/* v0.38 : simulateur de course de l'objectif principal — temps de passage (ambitieux / cible / prudent), barrières,
   allures par tronçon, « et si » (chaleur, forme du jour, temps visé), plan nutrition ravito par ravito, GPX privé. */
import { useEffect, useRef, useState, type ChangeEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { Heat, RaceLeg, RaceSim, ScenarioId, SimPoint, SimSection } from "../api/types";
import { ElevationProfile } from "../components/ElevationProfile";
import { dayLabel } from "../lib/format";
import { fmtHM, fmtLeg, fmtMargin, fmtPaceKm, parseTarget, shareText, STATUS_LABEL, targetText } from "../lib/race";
import { toast } from "../lib/toast";

const HEATS: [Heat, string][] = [["fraiche", "Fraîche"], ["normale", "Normale"], ["chaude", "Chaude"]];
const SCEN: [ScenarioId, string][] = [["ambitieux", "Ambitieux"], ["cible", "Cible"], ["prudent", "Prudent"]];
const KIND_ICON: Record<string, string> = { montee: "↗", descente: "↘", roulant: "→" };
const kmFr = (k: number): string => String(Math.round(k * 10) / 10).replace(".", ",");

interface Cond { heat: Heat; form: number; targetOn: boolean; target: string }

function Passages({ pts, sc }: { pts: SimPoint[]; sc: ScenarioId }): ReactElement {
  return (
    <ol className="sim-pass">
      {pts.map((p) => (
        <li key={p.name + p.km} className={"sim-pt " + p.kind}>
          <div className="sim-pt-main">
            <span className="sim-clock">{p.arrival[sc].clock}</span>
            <span className="sim-pt-name">{p.name}</span>
          </div>
          <div className="sim-pt-meta small muted">
            {`km ${kmFr(p.km)}` + (p.alt !== null ? ` · ${p.alt} m${p.alt_approx ? " (≈)" : ""}` : "")
              + (p.kind === "ravito" ? ` · ravito ${String(p.stop_min).replace(".", ",")} min` : p.kind === "eau" ? " · point d'eau" : "")
              + ` · ${p.arrival.ambitieux.clock} – ${p.arrival.prudent.clock}`}
          </div>
          {p.cutoff ? (
            <div className={"sim-cut " + p.cutoff.status}>
              <span className="badge">{`barrière ${p.cutoff.clock}`}</span>
              <span>{`marge ${fmtMargin(p.cutoff.margin_min[sc])} (${STATUS_LABEL[p.cutoff.status]} en prudent : ${fmtMargin(p.cutoff.margin_min.prudent)})`}</span>
              {p.cutoff.approx ? <span className="muted small">{p.cutoff.edition ? `édition ${p.cutoff.edition}, à confirmer` : "à confirmer"}</span> : null}
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function SectionCard({ s, sc }: { s: SimSection; sc: ScenarioId }): ReactElement {
  const t = s.time_s[sc];
  const speed = s.kind === "montee" && s.vam_m_h ? `${s.vam_m_h} m/h` : fmtPaceKm(t / Math.max(s.km, 0.01));
  return (
    <li className={"sim-sec " + s.kind}>
      <div className="sim-sec-head">
        <span className={"badge kind " + s.kind}><span aria-hidden="true">{KIND_ICON[s.kind]} </span>{s.kind_label}</span>
        <span className="small muted">{`km ${kmFr(s.km0)} → ${kmFr(s.km1)}`}</span>
      </div>
      <div className="sim-sec-title">{`${s.from} → ${s.to}`}</div>
      <div className="sim-sec-kpis">
        <div><b>{fmtLeg(t)}</b><small>temps</small></div>
        <div><b>{speed}</b><small>{s.kind === "montee" && s.vam_m_h ? "vitesse ascensionnelle" : "allure moyenne"}</small></div>
        <div><b>{`${kmFr(s.km)} km`}</b><small>{`+${s.dplus} / −${s.dminus} m`}</small></div>
      </div>
      {s.terrain.length > 1 ? (
        <ul className="sim-terrain">
          {s.terrain.map((x) => (
            <li key={x.kind}><span aria-hidden="true">{KIND_ICON[x.kind]} </span>{`${x.kind === "montee" ? "montées" : x.kind === "descente" ? "descentes" : "roulant"} ${kmFr(x.km)} km · `
              + (x.kind === "montee" && x.vam_m_h ? `${x.vam_m_h} m/h` : fmtPaceKm(x.pace_s_km))}</li>
          ))}
        </ul>
      ) : null}
      <div className="small">{s.effort}</div>
      {s.daylight ? <div className="small warnt">{s.daylight}</div> : null}
    </li>
  );
}

function LegCard({ l, i }: { l: RaceLeg; i: number }): ReactElement {
  return (
    <li className="sim-leg">
      <div className="sim-leg-head"><b>{i === 0 ? `Au départ` : `À ${l.from}`}</b>
        <span className="small muted">{`jusqu'à ${l.to} · ${kmFr(l.km)} km · ${fmtLeg(l.duration_s)}`}</span></div>
      <div className="sim-leg-kpis small">
        <span><b>{`${l.carbs_g} g`}</b>{` glucides (${l.carbs_g_h} g/h)`}</span>
        <span><b>{`${l.flasks} × 500 ml`}</b>{` (${l.fluid_ml} ml)`}</span>
        <span><b>{`${l.sodium_mg} mg`}</b>{" sodium"}</span>
        {l.caffeine_mg ? <span><b>{`${l.caffeine_mg} mg`}</b>{" caféine"}</span> : null}
      </div>
      {l.at_aid.length ? <div className="small"><span className="lbl">Au ravito :</span>{` ${l.at_aid_label}`}</div> : null}
      <div className="small"><span className="lbl">Emporte :</span>{` ${l.carry_label || "rien de plus"}`}
        {l.water_flasks ? `, ${l.water_flasks} flasque${l.water_flasks > 1 ? "s" : ""} d'eau` : ""}</div>
      {l.notes.map((n) => <div key={n} className="small muted">{n}</div>)}
    </li>
  );
}

function GpxCard({ d, onChanged }: { d: RaceSim; onChanged: () => void }): ReactElement {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const c = d.course;
  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!/\.(gpx|xml)$/i.test(f.name)) { setErr(`« ${f.name} » n'est pas un fichier GPX (.gpx).`); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api<{ gpx: { warning: string } }>(`/api/race/gpx?name=${encodeURIComponent(f.name)}`,
        { method: "POST", body: f, headers: { "Content-Type": "application/gpx+xml" } });
      toast(r.gpx.warning ? "GPX enregistré, avec un avertissement" : "GPX enregistré : calcul sur le tracé");
      onChanged();
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  const del = async () => {
    setBusy(true);
    try { await api("/api/race/gpx", { method: "DELETE" }); toast("GPX supprimé"); onChanged(); } finally { setBusy(false); }
  };
  return (
    <details className="card sim-gpx sim-fold" id="sim-gpx" open={!d.gpx && d.mode !== "approx" ? true : undefined}>
      <summary><h3>Le tracé (GPX)</h3><span className="small muted">{d.gpx ? d.gpx.name : "profil approximatif"}</span></summary>
      {d.gpx ? <>
        <div>{`${d.gpx.name} · ${kmFr(d.gpx.km)} km · ${d.gpx.dplus} m D+`}</div>
        {d.gpx.warning ? <div className="sim-warn" role="status">{d.gpx.warning}</div> : <div className="small good">Tracé conforme aux chiffres officiels : ravitos recalés sur leurs coordonnées GPS.</div>}
      </> : <div className="muted small">{d.mode_note}</div>}
      {d.gpx_error ? <div className="sim-warn">{d.gpx_error}</div> : null}
      {c?.track_url ? (
        <p className="small">Le tracé n'est pas public : télécharge la trace pour ton usage personnel sur{" "}
          <a href={c.track_url} target="_blank" rel="noopener noreferrer">Trace de Trail</a>, puis dépose-la ici.
          {" "}Elle reste sur ton serveur (dossier privé), jamais partagée ni sauvegardée ailleurs.</p>
      ) : <p className="small">Dépose le GPX de ta course (site de l'organisateur ou ta montre) : il reste sur ton serveur, dans ton dossier privé.</p>}
      <div className="row-actions">
        <label className={"btn small primary" + (busy ? " disabled" : "")} htmlFor="sim-gpx-file">{busy ? "Envoi…" : d.gpx ? "Remplacer le GPX" : "Déposer le GPX"}</label>
        <input ref={input} type="file" className="visually-hidden" disabled={busy} onChange={(e) => void pick(e)} id="sim-gpx-file" />
        {d.gpx ? <button className="btn small" disabled={busy} onClick={() => void del()}>Supprimer</button> : null}
      </div>
      {err ? <div className="error" role="alert">{err}</div> : null}
    </details>
  );
}

function ManualPoints({ d, onChanged }: { d: RaceSim; onChanged: () => void }): ReactElement | null {
  const [rows, setRows] = useState<{ name: string; km: string }[]>(() => (d.course?.manual_points || []).map((p) => ({ name: p.name, km: String(p.km) })));
  if (!d.course?.generic) return null;
  const save = async () => {
    const points = rows.map((r) => ({ name: r.name, km: Number(r.km.replace(",", ".")) })).filter((r) => r.km > 0);
    try { await api("/api/race/points", { json: { points } }); toast("Ravitos enregistrés"); onChanged(); } catch (e) { toast((e as Error).message); }
  };
  return (
    <section className="card" aria-labelledby="sim-pts-t">
      <h3 id="sim-pts-t">Ravitaillements</h3>
      <div className="muted small">Je ne connais pas encore les ravitos de cette course : indique leur kilomètre (règlement de la course).</div>
      <ul className="sim-manual">
        {rows.map((r, i) => (
          <li key={i}>
            <input aria-label="Nom du ravito" placeholder="Nom" value={r.name} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <input aria-label="Kilomètre" inputMode="decimal" placeholder="km" value={r.km} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, km: e.target.value } : x))} />
            <button className="btn small" aria-label="Retirer" onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
          </li>
        ))}
      </ul>
      <div className="row-actions">
        <button className="btn small" onClick={() => setRows([...rows, { name: "", km: "" }])}>Ajouter un ravito</button>
        <button className="btn small primary" onClick={() => void save()}>Enregistrer</button>
      </div>
    </section>
  );
}

/** v0.39 : ouvre la carte GPX (repliée) et le sélecteur de fichier. Avant, le lien changeait l'ancre de l'URL
    (#sim-gpx) et le routeur partait vers une vue inconnue : la page restait vide. */
function openGpx(): void {
  const card = document.getElementById("sim-gpx") as HTMLDetailsElement | null;
  if (card) { card.open = true; card.scrollIntoView({ behavior: "smooth", block: "start" }); }
  document.getElementById("sim-gpx-file")?.click();
}

export function Simulator({ active, nonce }: { active: boolean; nonce: number }): ReactElement {
  const [d, setD] = useState<RaceSim | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cond, setCond] = useState<Cond>({ heat: "normale", form: 0, targetOn: false, target: "" });
  const [sc, setSc] = useState<ScenarioId>("cible");
  const [reload, setReload] = useState(0);
  const first = useRef(true);
  const tgt = cond.targetOn ? parseTarget(cond.target) : null;

  useEffect(() => {
    if (!active) return;
    let stale = false;
    const q = new URLSearchParams({ heat: cond.heat, form: String(cond.form) });
    if (tgt) q.set("target", String(tgt));
    const run = () => {
      setBusy(true);
      api<RaceSim>(`/api/race/sim?${q}`).then((r) => {
        if (stale) return;
        setErr(null); setD(r);
        if (r.headline && !cond.target) setCond((c) => ({ ...c, target: targetText(Math.round(r.headline!.finish_s / 300) * 5) }));
      }).catch((e: Error) => { if (!stale) setErr(e.message); }).finally(() => { if (!stale) setBusy(false); });
    };
    const t = window.setTimeout(run, first.current ? 0 : 250);   // « et si » : recalcul en direct, sans rafale
    first.current = false;
    return () => { stale = true; window.clearTimeout(t); };
  }, [active, nonce, cond.heat, cond.form, tgt, reload]);

  const stepTarget = (delta: number) => {
    const m = parseTarget(cond.target) ?? (d?.headline ? Math.round(d.headline.finish_s / 60) : 555);
    setCond({ ...cond, targetOn: true, target: targetText(Math.max(30, m + delta)) });
  };
  const reset = () => setCond({ heat: "normale", form: 0, targetOn: false, target: d?.anchor ? targetText(Math.round(d.anchor.median_s / 300) * 5) : "" });

  let body: ReactElement;
  if (err && !d) body = <div className="empty">{err}</div>;
  else if (!d) body = <div className="empty">Simulation de ta course…</div>;
  else if (!d.available) {
    body = <div className="sim-grid">
      <section className="card">
        <div className="eyebrow">Simulateur de course</div>
        {d.goal ? <h2>{d.goal.name}</h2> : null}
        <p>{d.reason}</p>
        <a className="btn small" href="#coach">Parler au coach</a>
      </section>
      {d.need === "distance" && d.goal ? <GpxCard d={d} onChanged={() => setReload((n) => n + 1)} /> : null}
    </div>;
  } else {
    const h = d.headline!, n = d.nutrition!, pts = d.points!, approx = d.mode === "approx";
    const scen = d.scenarios!;
    const lines = n.legs.map((l, i) => `${i ? pts.find((p) => p.name === l.from)?.arrival[sc].clock + " " + l.from : "Départ"} → ${l.to} (${fmtLeg(l.duration_s)}) : `
      + `${l.carbs_g} g${l.at_aid.length ? ` — au ravito : ${l.at_aid_label}` : ""} — emporte : ${l.carry_label || "rien de plus"}, ${l.flasks} flasque(s)`);
    const share = shareText(d.course!.name, h.text, lines);
    const copy = async () => {
      try {
        if (navigator.share) await navigator.share({ title: "Plan de course", text: share });
        else { await navigator.clipboard.writeText(share); toast("Plan de course copié"); }
      } catch { /* partage annulé */ }
    };
    body = <div className="sim-grid" aria-busy={busy}>
      <section className="card sim-head" aria-labelledby="sim-title">
        <div className="card-head"><span className="eyebrow">Simulateur de course</span>
          {d.goal?.days_left !== null && d.goal?.days_left !== undefined ? <span className="badge cd">{`J-${d.goal.days_left}`}</span> : null}</div>
        <h2 id="sim-title">{d.course!.name}</h2>
        <div className="muted small">{[d.goal?.date ? dayLabel(d.goal.date) : "", `départ ${Number(d.course!.start_local.slice(11, 13))} h ${d.course!.start_local.slice(14, 16)}`,
          d.course!.start_place, `${kmFr(d.course!.official_km || d.total_km || 0)} km`, d.course!.official_dplus ? `${d.course!.official_dplus} m D+` : ""].filter(Boolean).join(" · ")}</div>
        <div className="sim-headline" aria-live="polite">
          <div className="muted small">Arrivée estimée</div>
          <div><span className="big">{h.finish_clock}</span><span className="sim-dur">{` (${fmtHM(h.finish_s)})`}</span></div>
          <div className="muted">{`fourchette ${h.lo_clock} – ${h.hi_clock}`}</div>
        </div>
        <div className="seg sim-scen" role="group" aria-label="Scénario affiché">
          {scen.map((s) => (
            <button key={s.id} className={sc === s.id ? "on" : ""} aria-pressed={sc === s.id} onClick={() => setSc(s.id)}>
              <span>{s.label}</span><b>{s.finish_clock}</b><small>{fmtHM(s.finish_s) + (s.itra_equiv ? ` · ITRA ${s.itra_equiv}` : "")}</small>
            </button>
          ))}
        </div>
        <div className={"sim-mode small" + (approx ? " approx" : "")}>{d.mode_note}{approx ? <> <button type="button" className="linkish" onClick={openGpx}>Ajouter le GPX</button></> : null}</div>
        {d.course!.start_note ? <div className="small muted">{d.course!.start_note}</div> : null}
      </section>

      <section className="card sim-whatif" aria-labelledby="sim-whatif-t">
        <h3 id="sim-whatif-t">Et si…</h3>
        <div className="sim-ctl">
          <div className="lbl" id="sim-heat-l">Chaleur</div>
          <div className="seg" role="group" aria-labelledby="sim-heat-l">
            {HEATS.map(([k, l]) => <button key={k} className={cond.heat === k ? "on" : ""} aria-pressed={cond.heat === k} data-heat={k}
              onClick={() => setCond({ ...cond, heat: k })}>{l}</button>)}
          </div>
        </div>
        <div className="sim-ctl">
          <label className="lbl" htmlFor="sim-form">{`Forme du jour : ${cond.form > 0 ? "+" : cond.form < 0 ? "−" : ""}${Math.abs(cond.form)} %`}</label>
          <input id="sim-form" type="range" min={-5} max={5} step={1} value={cond.form} disabled={cond.targetOn}
            onChange={(e) => setCond({ ...cond, form: Number(e.target.value) })} />
          <div className="sim-range small muted"><span>jour sans</span><span>grand jour</span></div>
        </div>
        <div className="sim-ctl">
          <label className="sim-check"><input type="checkbox" checked={cond.targetOn} onChange={(e) => setCond({ ...cond, targetOn: e.target.checked })} />
            <span>Viser un temps</span></label>
          <div className="sim-target">
            <button className="btn small" aria-label="5 minutes de moins" onClick={() => stepTarget(-5)}>−5</button>
            <input id="sim-target" aria-label="Temps visé (h:mm)" inputMode="numeric" value={cond.target}
              onChange={(e) => setCond({ ...cond, target: e.target.value, targetOn: true })}
              aria-invalid={cond.targetOn && !tgt} />
            <button className="btn small" aria-label="5 minutes de plus" onClick={() => stepTarget(5)}>+5</button>
          </div>
          {cond.targetOn && !tgt ? <div className="error small">Temps visé : par exemple 9:00</div> : null}
          {cond.targetOn ? <div className="small muted">Le temps visé est réparti selon le terrain : plus de temps dans les montées, pas une allure uniforme.</div> : null}
        </div>
        <button className="btn small" onClick={reset}>Revenir au pronostic</button>
        <div className="small muted sim-anchor">{`Calé sur ${d.anchor!.source}.`}</div>
      </section>

      <section className="card span2" aria-labelledby="sim-prof-t">
        <h3 id="sim-prof-t">Profil</h3>
        <ElevationProfile profile={d.profile!} points={pts} scenario={sc} approx={approx} legs={n.legs} />
        {d.course!.altitude_note ? <div className="chart-note">{d.course!.altitude_note}</div> : null}
        <div className="chart-note">{d.daylight!.note}</div>
      </section>

      <details className="card sim-fold">
        <summary><h3>{`Temps de passage (${SCEN.find((x) => x[0] === sc)![1].toLowerCase()})`}</h3><span className="small muted">{`${pts.length - 2} points · arrivée ${pts[pts.length - 1].arrival[sc].clock}`}</span></summary>
        <Passages pts={pts} sc={sc} />
        {d.course!.cutoff_note ? <div className="chart-note">{d.course!.cutoff_note}</div> : null}
      </details>

      {d.climbs!.length ? (
        <details className="card sim-fold">
          <summary><h3>Les montées</h3><span className="small muted">{`${d.climbs!.length} montée${d.climbs!.length > 1 ? "s" : ""}`}</span></summary>
          <ul className="sim-climbs">
            {d.climbs!.map((c) => (
              <li key={c.km0}><b>{c.name}</b><div className="small muted">{`km ${kmFr(c.km0)} → ${kmFr(c.km1)} · +${c.gain_m} m · ${String(c.grade_pct).replace(".", ",")} % de moyenne`}</div>
                <div className="small">{`${fmtLeg(c.time_s)}` + (c.vam_m_h && c.grade_pct >= 6 ? ` · ${c.vam_m_h} m/h` : "")}</div></li>
            ))}
          </ul>
          <div className="chart-note">Vitesse ascensionnelle du scénario cible : c'est l'effort à travailler en côte pendant la phase spécifique.</div>
        </details>
      ) : null}

      <details className="card span2 sim-fold">
        <summary><h3>Allures par tronçon</h3><span className="small muted">{`${d.sections!.length} tronçons`}</span></summary>
        <ol className="sim-secs">{d.sections!.map((s) => <SectionCard key={s.i} s={s} sc={sc} />)}</ol>
      </details>

      <details className="card span2 sim-nutri sim-fold">
        <summary><h3>Plan nutrition</h3><span className="small muted">{`${n.target_g_h} g/h · ${n.fluid_ml_h} ml/h · ${n.sodium_mg_h} mg/h de sodium`}</span></summary>
        <div className="nutri-sum">
          <div><b>{`${n.target_g_h} g/h`}</b><small>{`glucides (${n.range_g_h[0]}-${n.range_g_h[1]})`}</small></div>
          <div><b>{`${n.fluid_ml_h} ml/h`}</b><small>eau</small></div>
          <div><b>{`${n.sodium_mg_h} mg/h`}</b><small>sodium</small></div>
        </div>
        {n.notes.map((x) => <div key={x} className={"small" + (n.rehearsal_needed && x.startsWith("Aucun") ? " sim-warn" : "")}>{x}</div>)}
        <div className="small">{n.heat_note}</div>
        <div className="small">{n.caffeine_note}</div>
        <ol className="sim-legs">{n.legs.map((l, i) => <LegCard key={l.from + i} l={l} i={i} />)}</ol>
        <div className="small"><span className="lbl">Obligatoire :</span>{` ${n.mandatory.join(" ; ")}.`}</div>
        {n.aid_food_label ? <div className="small muted">{`Aux ravitos (règlement ${d.course!.sources.find((s) => s.what.startsWith("règlement"))?.edition ?? ""}) : ${n.aid_food_label}.`}</div> : null}
      </details>

      <details className="card span2 sim-print sim-fold">
        <summary><h3>Plan de course</h3><span className="small muted">à imprimer ou partager</span></summary>
          <div className="row-actions sim-print-actions">
            <button className="btn small" onClick={() => void copy()}>Partager</button>
            <button className="btn small" onClick={() => window.print()}>Imprimer</button>
          </div>
        <div className="small muted">{`${h.text} · ${HEATS.find((x) => x[0] === d.conditions!.heat)![1].toLowerCase()} · ${n.target_g_h} g/h`}</div>
        <ol className="sim-mini">
          {n.legs.map((l, i) => {
            const p = pts.find((x) => x.name === l.from);
            return (
              <li key={l.from + i}>
                <span className="sim-clock">{i === 0 ? pts[0].arrival[sc].clock : p?.arrival[sc].clock}</span>
                <span className="sim-mini-t"><b>{i === 0 ? "Départ" : l.from}</b>{` → ${l.to} · ${fmtLeg(l.duration_s)}`}
                  <span className="sim-mini-d">{(l.at_aid.length ? `Ravito : ${l.at_aid_label}. ` : "") + `Emporte : ${l.carry_label || "—"}, ${l.flasks} × 500 ml.`}</span></span>
              </li>
            );
          })}
          <li><span className="sim-clock">{pts[pts.length - 1].arrival[sc].clock}</span><span className="sim-mini-t"><b>{pts[pts.length - 1].name}</b></span></li>
        </ol>
      </details>

      <GpxCard d={d} onChanged={() => setReload((x) => x + 1)} />
      <ManualPoints key={(d.course!.manual_points || []).length} d={d} onChanged={() => setReload((x) => x + 1)} />

      <section className="card">
        <details className="alt">
          <summary>Comment c'est calculé</summary>
          {d.model_notes!.map((x) => <p key={x} className="small">{x}</p>)}
          {d.course!.terrain ? <p className="small">{`Terrain : ${Math.round(d.course!.terrain.paths * 100)} % de sentiers, ${Math.round(d.course!.terrain.tracks * 100)} % de pistes, ${Math.round(d.course!.terrain.road * 100)} % de route (${d.course!.terrain.source}).`}</p> : null}
          {d.course!.time_limit_note ? <p className="small">{d.course!.time_limit_note}</p> : null}
          <ul className="small">{d.course!.sources.map((s) => <li key={s.what}>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.what}</a> : s.what}{` (vu le ${s.checked})`}</li>)}</ul>
          <p className="small muted">{`Calcul en ${String(d.compute_ms).replace(".", ",")} ms.`}</p>
        </details>
      </section>
    </div>;
  }
  return <section id="view-course" className="view" data-view="course" hidden={!active}>{body}</section>;
}
