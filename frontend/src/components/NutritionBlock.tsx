/* v0.38 : bloc « Nutrition » de la fiche de retour, pour les sorties ≥ 1 h 30.
   Rapide à remplir au téléphone : familles en puces, − / + par aliment, puces de symptômes en un toucher. */
import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { NutritionFoods, NutritionLog } from "../api/types";
import { carbsVerdict, FLASK_ML, fmtHours, liveTotals, targetCarbs, type Picks } from "../lib/nutrition";

export interface NutritionValue { picks: Picks; water: number; digestion: number | null; symptoms: string[]; comment: string }
export const EMPTY_NUTRITION: NutritionValue = { picks: {}, water: 0, digestion: null, symptoms: [], comment: "" };

export const nutritionTouched = (v: NutritionValue): boolean =>
  Object.values(v.picks).some((p) => p.qty > 0) || v.water > 0 || v.digestion !== null || v.symptoms.length > 0 || !!v.comment.trim();

export function fromLog(l: NutritionLog | null | undefined): NutritionValue {
  if (!l) return EMPTY_NUTRITION;
  const picks: Picks = {};
  for (const it of l.items) picks[it.id] = { qty: it.qty, carbs: it.carbs_g };
  return { picks, water: l.water_ml, digestion: l.digestion, symptoms: [...l.symptoms], comment: l.comment };
}

let cache: NutritionFoods | null = null;
const HIDDEN = new Set(["eau"]);                   // l'eau a son propre compteur (flasques)

function Stepper({ value, onChange, label, step = 1 }: { value: number; onChange: (v: number) => void; label: string; step?: number }): ReactElement {
  return (
    <div className="stepper" role="group" aria-label={label}>
      <button type="button" aria-label={`Moins : ${label}`} disabled={value <= 0} onClick={() => onChange(Math.max(0, value - step))}>−</button>
      <output aria-live="polite">{value % 1 ? value.toFixed(1).replace(".", ",") : value}</output>
      <button type="button" aria-label={`Plus : ${label}`} onClick={() => onChange(value + step)}>+</button>
    </div>
  );
}

export function NutritionBlock({ minutes, value, onChange }: { minutes: number; value: NutritionValue; onChange: (v: NutritionValue) => void }): ReactElement {
  const [cat, setCat] = useState<NutritionFoods | null>(cache);
  const [fam, setFam] = useState("gel");
  const [own, setOwn] = useState<string | null>(null);        // aliment dont on corrige le grammage
  useEffect(() => {
    if (cache) return;
    api<NutritionFoods>("/api/nutrition/foods").then((r) => { cache = r; setCat(r); }).catch(() => {});
  }, []);
  const hours = minutes / 60;
  const foods = cat?.foods ?? [];
  const t = liveTotals(foods, value.picks, value.water, hours);
  const target = targetCarbs(hours, cat?.trained_max_g_h);
  const verdict = carbsVerdict(t.carbs_g_h, target);
  const set = (patch: Partial<NutritionValue>) => onChange({ ...value, ...patch });
  const setQty = (id: string, qty: number) => set({ picks: { ...value.picks, [id]: { qty, carbs: value.picks[id]?.carbs ?? null } } });
  const setOwnCarbs = (id: string, carbs: number | null) => set({ picks: { ...value.picks, [id]: { qty: value.picks[id]?.qty ?? 0, carbs } } });
  const count = (f: string) => foods.filter((x) => x.family === f).reduce((n, x) => n + (value.picks[x.id]?.qty || 0), 0);
  const chosen = foods.filter((f) => (value.picks[f.id]?.qty || 0) > 0);
  const scale = Math.max(target[1] + 20, t.carbs_g_h + 10, 100);

  return (
    <fieldset className="nutri" id="fb-nutri">
      <legend className="label">Nutrition <span className="muted small">{`· sortie de ${fmtHours(hours)}`}</span></legend>
      <div className="nutri-live" aria-live="polite">
        <div className={`nutri-kpi carbs ${verdict}`}>
          <b>{t.carbs_g_h}</b><span>g/h</span>
          <small>{`glucides · cible ${target[0]}-${target[1]}`}{verdict === "bas" ? " · un peu bas" : verdict === "haut" ? " · au-dessus" : " · dans la cible"}</small>
        </div>
        <div className="nutri-kpi"><b>{t.sodium_mg_h}</b><span>mg/h</span><small>sodium</small></div>
        <div className="nutri-kpi"><b>{t.fluid_ml_h}</b><span>ml/h</span><small>bu</small></div>
        <div className="nutri-meter" aria-hidden="true">
          <span className="band" style={{ left: `${(100 * target[0]) / scale}%`, width: `${(100 * (target[1] - target[0])) / scale}%` }}></span>
          <i style={{ width: `${Math.min(100, (100 * t.carbs_g_h) / scale)}%` }}></i>
        </div>
      </div>
      {chosen.length ? <div className="nutri-chosen small">{chosen.map((f) => `${f.name.split(" (")[0]} ×${String(value.picks[f.id].qty).replace(".", ",")}`).join(" · ")}</div>
        : <div className="nutri-chosen small muted">Touche + sur ce que tu as pris.</div>}
      <div className="nutri-fams" role="tablist" aria-label="Familles d'aliments">
        {(cat?.families ?? []).map((f) => {
          const n = count(f.id);
          return <button key={f.id} type="button" role="tab" aria-selected={fam === f.id} className={"chip" + (fam === f.id ? " on" : "")}
            onClick={() => setFam(f.id)}>{f.label}{n ? <span className="nutri-count">{n % 1 ? n.toFixed(1).replace(".", ",") : n}</span> : null}</button>;
        })}
      </div>
      <ul className="nutri-list" role="tabpanel">
        {foods.filter((f) => f.family === fam && !HIDDEN.has(f.id)).map((f) => {
          const p = value.picks[f.id];
          const q = p?.qty || 0;
          const g = p?.carbs ?? f.carbs_g;
          return (
            <li key={f.id} className={"nutri-item" + (q ? " on" : "")} data-food={f.id}>
              <div className="ni-txt">
                <div className="ni-name">{f.name}</div>
                <div className="ni-meta">{`${f.portion} · ${g} g${f.sodium_mg ? ` · ${f.sodium_mg} mg Na` : ""}`}</div>
                {q ? (own === f.id ? (
                  <label className="ni-own">Mon produit :
                    <input type="number" inputMode="decimal" min={0} max={200} step={1} defaultValue={p?.carbs ?? f.carbs_g} autoFocus
                      onBlur={(e) => { const v = parseFloat(e.target.value); setOwnCarbs(f.id, Number.isFinite(v) && v !== f.carbs_g ? v : null); setOwn(null); }} /> g
                  </label>
                ) : <button type="button" className="link-btn ni-own" onClick={() => setOwn(f.id)}>{p?.carbs != null ? `mon produit : ${p.carbs} g` : "mon produit a un autre grammage"}</button>) : null}
              </div>
              <Stepper label={f.name} value={q} onChange={(v) => setQty(f.id, v)} />
            </li>
          );
        })}
      </ul>
      <div className="nutri-water">
        <div className="ni-txt"><div className="ni-name">Eau</div><div className="ni-meta">{`flasques de ${FLASK_ML} ml`}</div></div>
        <Stepper label="Flasques d'eau" value={Math.round((value.water / FLASK_ML) * 2) / 2} step={1} onChange={(v) => set({ water: Math.round(v * FLASK_ML) })} />
        <label className="ni-ml"><input type="number" inputMode="numeric" min={0} max={20000} step={50} aria-label="Eau en ml" value={value.water || ""}
          placeholder="0" onChange={(e) => set({ water: Math.max(0, parseInt(e.target.value || "0", 10) || 0) })} /> ml</label>
      </div>
      <div className="field">
        <span className="label" id="nutri-dig-l">Digestion</span>
        <div className="seg nutri-dig" role="radiogroup" aria-labelledby="nutri-dig-l">
          {(cat?.digestion ?? []).map((d) => (
            <button key={d.v} type="button" role="radio" aria-checked={value.digestion === d.v} className={(value.digestion === d.v ? "on " : "") + `d${d.v}`}
              onClick={() => set({ digestion: value.digestion === d.v ? null : d.v })}><b>{d.v}</b><small>{d.label}</small></button>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="label">Symptômes</span>
        <div className="nutri-syms">
          {(cat?.symptoms ?? []).map((s) => {
            const on = value.symptoms.includes(s.id);
            return <button key={s.id} type="button" aria-pressed={on} className={"chip" + (on ? " on" : "")} data-sym={s.id}
              onClick={() => set({ symptoms: on ? value.symptoms.filter((x) => x !== s.id) : [...value.symptoms, s.id] })}>{s.label}</button>;
          })}
        </div>
      </div>
      <textarea className="nutri-comment" rows={2} maxLength={1000} placeholder="Ressenti, ce qui a coincé, ce que tu veux essayer…" value={value.comment}
        onChange={(e) => set({ comment: e.target.value })} aria-label="Commentaire nutrition" />
    </fieldset>
  );
}
