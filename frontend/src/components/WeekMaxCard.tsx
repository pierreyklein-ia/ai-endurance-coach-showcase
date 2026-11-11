/* v0.49 : « Mon volume maximum » (Carrière › Réglages) : le plafond que le plan ne dépasse jamais. */
import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { WeekMaxSaved, WeekMaxView } from "../api/types";
import { toast } from "../lib/toast";
import { fmtHours, hoursField, parseHours, parseNum, weekMaxError } from "../lib/weekMax";

export function WeekMaxCard({ active, nonce, onChat }: { active: boolean; nonce: number; onChat?: () => void }): ReactElement | null {
  const [v, setV] = useState<WeekMaxView | null>(null);
  const [gen, setGen] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<WeekMaxView>("/api/week-max").then((r) => { if (!stale) { setV(r); setGen((g) => g + 1); } }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce]);
  if (!v || !v.has_profile) return null;

  const save = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const form = { hours: parseHours(String(f.get("hours") || "")), km: parseNum(String(f.get("km") || "")),
      dplus: parseNum(String(f.get("dplus") || "")) };
    const bad = weekMaxError(form);
    setErr(bad || "");
    if (bad) return;
    setBusy(true);
    try {
      const r = await api<WeekMaxSaved>("/api/week-max", { json: { hours: form.hours, km: form.km, dplus: form.dplus === null ? null : Math.round(form.dplus) } });
      toast(r.replan ? "Enregistré ✓ Ton plan dépassait : le coach te propose un plan ajusté dans le chat" : r.text ? `Enregistré ✓ ${r.text}` : "Enregistré ✓ Pas de plafond", 6000);
      const nv = await api<WeekMaxView>("/api/week-max");
      setV(nv); setGen((g) => g + 1);
      if (r.replan) onChat?.();
    } catch (er) { toast((er as Error).message); }
    setBusy(false);
  };

  return (
    <div className="card week-max" id="week-max">
      <div className="eyebrow">Mon volume maximum</div>
      <div className="wm-now">{v.text ? <><b>{v.text}</b>{v.inferred ? <span className="muted small"> · estimé d'après ton historique</span> : null}</> : <span className="muted">Pas de plafond</span>}</div>
      <div className="muted small">Le plus que tu peux faire sur une semaine. Le plan et le coach ne vont jamais au-delà : les footings sont raccourcis d'abord, puis la sortie longue.
        {v.best_4w_min ? ` Ta plus grosse période récente : ${fmtHours(v.best_4w_min)} par semaine sur 4 semaines.` : ""}</div>
      <form key={gen} className="wm-form" onSubmit={save} noValidate>
        <label className="wm-f"><span className="small">Temps</span>
          <input name="hours" inputMode="decimal" placeholder="ex. 6h30" defaultValue={hoursField(v.minutes)} aria-label="Temps maximum par semaine (heures)" /></label>
        <label className="wm-f"><span className="small">Distance (km)</span>
          <input name="km" inputMode="decimal" placeholder="facultatif" defaultValue={v.km ?? ""} aria-label="Distance maximum par semaine (km)" /></label>
        <label className="wm-f"><span className="small">D+ (m)</span>
          <input name="dplus" inputMode="numeric" placeholder="facultatif" defaultValue={v.dplus ?? ""} aria-label="Dénivelé positif maximum par semaine (m)" /></label>
        <button className="btn small primary" type="submit" disabled={busy}>{busy ? "…" : "Enregistrer"}</button>
      </form>
      {err ? <div className="error small" role="alert">{err}</div> : <div className="muted small">Laisse vide pour ne pas fixer de plafond.</div>}
    </div>
  );
}
