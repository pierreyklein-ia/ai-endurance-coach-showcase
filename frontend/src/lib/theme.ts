/* v0.29 : thèmes à l'essai, choisis dans Carrière › Réglages et gardés sur cet appareil */
import { lsGet, lsSet } from "./format";

export const THEMES: [string, string][] = [["classique", "Classique"], ["bd", "BD"], ["crepuscule", "Crépuscule"], ["toon", "Cel-shading"]];
export const THEME_COLOR: Record<string, string> = { classique: "#12805c", bd: "#ff7a1a", crepuscule: "#46437e", toon: "#4bb9ec" };

export function applyTheme(t: string | null): string {
  const theme = t && THEMES.some(([k]) => k === t) ? t : "classique";
  document.documentElement.dataset.theme = theme;
  lsSet("theme", theme);
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute("content", THEME_COLOR[theme]));
  return theme;
}
export const currentTheme = (): string => document.documentElement.dataset.theme || "classique";
export const initTheme = (): string => applyTheme(lsGet("theme"));
