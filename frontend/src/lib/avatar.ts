/* carrière (v0.24) : palette de l'avatar et équipement porté */
import type { AvatarCfg, GearItem } from "../api/types";

export const AV = {
  skin: ["#f6d7c3", "#eac0a0", "#d9a27e", "#b97a55", "#8d5a3b", "#5e3b26"],
  hairColor: ["#1f1a17", "#4a2f1f", "#8a5a2b", "#c99a4f", "#b8b2a8", "#a83a2a"],
  shirt: ["#1f7a4d", "#2563eb", "#dc2626", "#f59e0b", "#7c3aed", "#0f172a", "#ec4899", "#14b8a6"],
  shorts: ["#0f172a", "#334155", "#1e3a8a", "#14532d", "#7f1d1d", "#e5e7eb"],
  shoes: ["#f97316", "#22c55e", "#e11d48", "#3b82f6", "#facc15", "#f8fafc"],
  hair: [["court", "Court"], ["long", "Queue de cheval"], ["chignon", "Chignon"], ["boucles", "Bouclés"], ["rase", "Rasé"]] as [string, string][],
};
export const TIER: Record<number, [string, string]> = { 1: ["bronze", "#b87333"], 2: ["argent", "#9ca3af"], 3: ["or", "#eab308"], 4: ["légende", "#8b5cf6"] };

/** v0.33 : un seul chapeau à la fois ; il remplace casquette, bandeau et laurier. */
export const HATS = ["chapeau_sorciere", "bonnet_noel", "chapeau_fete"];

/** Équipement réellement porté (même règle que coach/career.py::worn_gear) : choix de l'athlète, ou en automatique
 *  tout ce qui est débloqué sauf les accessoires de saison hors de leur période ; casquette et bandeau exclusifs. */
export function wornGear(cfg: Pick<AvatarCfg, "gear">, gear: GearItem[] | null | undefined): Set<string> {
  const list = gear || [];
  const unlocked = new Set(list.filter((g) => g.unlocked).map((g) => g.id));
  const offSeason = new Set(list.filter((g) => g.seasonal && !g.in_season).map((g) => g.id));
  const auto = cfg.gear === null || cfg.gear === undefined;
  let w = auto ? [...unlocked].filter((g) => !offSeason.has(g)) : cfg.gear!.filter((g) => unlocked.has(g));
  const hat = HATS.find((h) => w.includes(h));
  if (hat) w = w.filter((g) => !["casquette", "bandeau", "laurier"].includes(g) && (!HATS.includes(g) || g === hat));
  const out = new Set(w);
  if (out.has("casquette")) out.delete("bandeau");
  return out;
}
