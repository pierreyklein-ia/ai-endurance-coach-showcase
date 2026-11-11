/* Formats français, repris tels quels de l'interface historique (app.js). */
export const JOURS = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];
export const MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
export const STATUS: Record<string, string> = { done: "Faite", modified: "Modifiée", skipped: "Sautée" };

export const d8 = (iso: string): Date => new Date(iso + "T12:00:00");
export const tms = (iso: string): number => d8(iso).getTime();
export const dayLabel = (iso: string): string => {
  const d = d8(iso);
  return `${JOURS[d.getDay()]} ${d.getDate()} ${MOIS[d.getMonth()]}`;
};
export const fmtMin = (m: number | null | undefined): string =>
  !m ? "" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? String(m % 60).padStart(2, "0") : ""}`;
export const timeOf = (iso: string): string =>
  new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
export const isoDay = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const frDateTime = (iso: string): string => {
  const d = new Date(iso);
  return `${d.getDate()}/${String(d.getMonth() + 1).padStart(2, "0")} à ${String(d.getHours()).padStart(2, "0")} h ${String(d.getMinutes()).padStart(2, "0")}`;
};
export function addDays(iso: string, n: number): string {
  const d = d8(iso);
  d.setDate(d.getDate() + n);
  return isoDay(d);
}
export const eur = (x: number | null | undefined): string => (x ?? 0).toFixed(2).replace(".", ",") + " €";
export const signed = (x: number | null | undefined): string =>
  x === null || x === undefined ? "n/d" : (x > 0 ? "+" : "") + x;
export const fmtDur = (s0: number | null | undefined): string => {
  if (s0 === null || s0 === undefined) return "n/d";
  const s = Math.round(s0);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh} h ${String(mm).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
};
export const fmtPace = (s: number): string => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}/km`;
export const fr1 = (x: number | null | undefined): string =>
  x === null || x === undefined ? "n/d" : String(Math.round(x * 10) / 10).replace(".", ",");
export const comma = (x: number): string => String(x).replace(".", ",");
export const frInt = (n: number): string => n.toLocaleString("fr-FR");

export function niceTicks(lo: number, hi: number, n = 4): number[] {
  const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

/** Ajoute un texte dicté à ce qui était déjà écrit. */
export const joinBase = (base: string, text: string): string =>
  base && text ? base.replace(/\s+$/, "") + " " + text.trim() : (base || "") + (text || "").trim();

export const lsGet = (k: string): string | null => {
  try { return localStorage.getItem(k); } catch { return null; }
};
export const lsSet = (k: string, v: string): void => {
  try { localStorage.setItem(k, v); } catch { /* navigation privée */ }
};
