/* v0.37 : présentation des objectifs (principal, secondaires, projets) — fonctions pures, testées. */
import type { GoalItem } from "../api/types";
import { d8, MOIS } from "./format";

const JOURS_LONG = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];

/** « samedi 8 nov. 2026 », « 2028, date à préciser », « date à préciser » */
export function goalWhen(g: Pick<GoalItem, "date" | "year">): string {
  if (g.date) {
    const d = d8(g.date);
    return `${JOURS_LONG[d.getDay()]} ${d.getDate()} ${MOIS[d.getMonth()]} ${d.getFullYear()}`;
  }
  return g.year ? `${g.year}, date à préciser` : "date à préciser";
}

/** « 21,1 km · 300 m D+ · route » (ce qui est connu) */
export function goalFacts(g: Pick<GoalItem, "km" | "dplus" | "kind">): string {
  return [g.km ? `${String(g.km).replace(".", ",")}\u00a0km` : "", g.dplus ? `${g.dplus}\u00a0m\u00a0D+` : "",
    g.kind === "route" ? "route" : ""].filter(Boolean).join(" · ");
}

/** « J-15 », « aujourd'hui », « terminé », « » (date inconnue) */
export function countdown(g: Pick<GoalItem, "days_left" | "done">): string {
  if (g.done) return "terminé";
  if (g.days_left === null) return "";
  return g.days_left === 0 ? "aujourd'hui" : `J-${g.days_left}`;
}

/** Groupes dans l'ordre d'affichage ; chaque groupe garde l'ordre du serveur (par date, le plus proche d'abord). */
export function groupGoals(goals: GoalItem[]): { main: GoalItem | null; secondary: GoalItem[]; projects: GoalItem[] } {
  const byDate = (a: GoalItem, b: GoalItem) =>
    Number(a.done) - Number(b.done) || Number(a.date === null) - Number(b.date === null) || (a.date || "").localeCompare(b.date || "");
  return {
    main: goals.find((g) => g.role === "A") || null,
    secondary: goals.filter((g) => g.role === "B").sort(byDate),
    projects: goals.filter((g) => g.role === "C").sort(byDate),
  };
}

/** v0.37 : principal d'abord, puis secondaires du plus proche au plus lointain (le serveur trie déjà ; on garantit l'ordre). */
export const sortGoalCards = <T extends { role?: string; date: string | null }>(gs: T[]): T[] =>
  [...gs].sort((a, b) => Number((a.role || "A") !== "A") - Number((b.role || "A") !== "A")
    || Number(a.date === null) - Number(b.date === null) || (a.date || "").localeCompare(b.date || ""));
