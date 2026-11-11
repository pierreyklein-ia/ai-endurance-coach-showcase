/* v0.38 : bilan nutrition des sorties longues — calculs en direct (même formules que coach/nutrition_foods.py). */
import type { Food, NutritionItem } from "../api/types";

export const FLASK_ML = 500;

/** Quantités saisies : { id: { qty, carbs } } (carbs = grammage de SON produit, optionnel). */
export type Picks = Record<string, { qty: number; carbs: number | null }>;

export function toItems(p: Picks): NutritionItem[] {
  return Object.entries(p).filter(([, v]) => v.qty > 0).map(([id, v]) => ({ id, qty: v.qty, carbs_g: v.carbs }));
}

/** Glucides, sodium, liquide par heure (l'eau seule s'ajoute au liquide). */
export function liveTotals(foods: Food[], p: Picks, waterMl: number, hours: number): { carbs_g_h: number; sodium_mg_h: number; fluid_ml_h: number; carbs_g: number } {
  const by = new Map(foods.map((f) => [f.id, f]));
  let c = 0, na = 0, ml = waterMl;
  for (const it of toItems(p)) {
    const f = by.get(it.id); if (!f) continue;
    c += it.qty * (it.carbs_g ?? f.carbs_g);
    na += it.qty * f.sodium_mg;
    ml += it.qty * f.fluid_ml;
  }
  const h = Math.max(hours, 0.25);
  return { carbs_g: Math.round(c), carbs_g_h: Math.round(c / h), sodium_mg_h: Math.round(na / h), fluid_ml_h: Math.round(ml / h) };
}

/** Fourchette de glucides par heure (miroir de nutrition_foods.target_carbs_g_h). */
export function targetCarbs(hours: number, trainedMax: number | null | undefined): [number, number] {
  let lo = hours < 2.5 ? 30 : 60, hi = hours < 2.5 ? 60 : 90;
  if (trainedMax) {
    const tm = Math.trunc(trainedMax);
    hi = Math.min(Math.max(hi, tm), tm + 10);
    lo = Math.min(lo, hi);
  }
  return [lo, Math.min(hi, 120)];
}

/** Où se situe l'apport par rapport à la cible. */
export function carbsVerdict(g: number, [lo, hi]: [number, number]): "bas" | "ok" | "haut" {
  return g < lo ? "bas" : g > hi ? "haut" : "ok";
}

export function fmtHours(h: number): string {
  const m = Math.round(h * 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
}
