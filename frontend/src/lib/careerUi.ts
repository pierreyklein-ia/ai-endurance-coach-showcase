/* v0.49 : la carrière se voit (anneau d'XP autour de l'avatar, pastille de niveau, bulle d'aide, carte « Ma carrière »). */
import type { Career, Trophy } from "../api/types";
import { frInt, lsGet, lsSet } from "./format";

const FAMILY_ORDER: Record<string, string[]> = {
  route: ["vitesse", "jalons", "regularite", "force", "intelligence", "endurance"],
  trail: ["endurance", "jalons", "force", "regularite", "intelligence", "vitesse"],
};
const ALWAYS = ["evenements"];             // v0.33 : les événements du calendrier, pour tout le monde

/** Progression vers le niveau suivant, bornée à 0-100 %. */
export function xpProgress(c: Pick<Career, "xp" | "next_at" | "pct" | "level">): { pct: number; toNext: number; next: number } {
  const pct = Math.max(0, Math.min(100, Number.isFinite(c.pct) ? c.pct : 0));
  return { pct, toNext: Math.max(0, c.next_at - c.xp), next: c.level + 1 };
}

/** Anneau SVG : longueur du trait plein pour un cercle de rayon `r` (stroke-dasharray / dashoffset). */
export function ringDash(pct: number, r: number): { circ: number; offset: number } {
  const circ = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(100, pct));
  return { circ, offset: circ * (1 - p / 100) };
}

/** Nouveaux trophées ou niveau gagné : la pastille du haut clignote. */
export const hasNews = (c: Pick<Career, "new" | "level_up"> | null): boolean => !!c && ((c.new || []).length > 0 || !!c.level_up);

/** Libellé accessible du bouton de l'avatar. */
export function careerLabel(c: Pick<Career, "xp" | "next_at" | "pct" | "level" | "new" | "level_up">): string {
  const { toNext, next } = xpProgress(c);
  const n = (c.new || []).length;
  const news = c.level_up ? " Nouveau niveau !" : n ? ` ${n > 1 ? `${n} nouveaux trophées` : "1 nouveau trophée"} !` : "";
  return `Ma carrière : niveau ${c.level}, ${frInt(toNext)} XP avant le niveau ${next}.${news}`;
}

/** Ordre des familles : selon la discipline (route ou trail), puis les événements. */
export function familyOrder(c: Pick<Career, "discipline" | "families">): string[] {
  const base = (c.discipline && FAMILY_ORDER[c.discipline]) || Object.keys(c.families).filter((f) => !ALWAYS.includes(f));
  return [...base, ...ALWAYS.filter((f) => f in c.families && !base.includes(f))];
}

/** Prochains objectifs : le plus petit palier encore à gagner dans chaque famille (dans l'ordre des familles). */
export function nextTrophies(c: Pick<Career, "discipline" | "families" | "trophies">, n = 99): Trophy[] {
  return familyOrder(c)
    .map((f) => c.trophies.filter((t) => t.family === f && !t.day).sort((a, b) => a.tier - b.tier || a.xp - b.xp)[0])
    .filter((t): t is Trophy => !!t)
    .slice(0, n);
}

/** Bulle « Touche ton avatar… » : une seule fois par appareil. */
export const HINT_KEY = "coach.careerHint";
export const hintSeen = (): boolean => lsGet(HINT_KEY) === "1";
export const markHintSeen = (): void => lsSet(HINT_KEY, "1");
