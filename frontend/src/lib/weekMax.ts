/* v0.49 : « Mon volume maximum » (Carrière › Réglages) : saisie libre des heures, affichage. */

/** « 6 », « 6,5 », « 6h30 », « 6 h », « 90 min » -> heures ; vide -> null ; illisible -> NaN. */
export function parseHours(raw: string): number | null {
  const t = raw.trim().toLowerCase().replace(",", ".");
  if (!t) return null;
  let m = /^(\d+(?:\.\d+)?)\s*(?:h|heures?)?\s*(\d{1,2})?\s*(?:min)?$/.exec(t);
  if (m && !/min$/.test(t)) return +m[1] + (m[2] ? +m[2] / 60 : 0);
  m = /^(\d+)\s*min$/.exec(t);
  if (m) return +m[1] / 60;
  return NaN;
}

/** Nombre positif ou vide (null) ; illisible -> NaN. */
export function parseNum(raw: string): number | null {
  const t = raw.trim().replace(",", ".").replace(/\s/g, "");
  if (!t) return null;
  const x = Number(t);
  return Number.isFinite(x) && x >= 0 ? x : NaN;
}

/** 390 -> « 6 h 30 », 360 -> « 6 h ». */
export function fmtHours(minutes: number | null | undefined): string {
  if (!minutes) return "";
  const m = Math.round(minutes / 5) * 5;
  const h = Math.floor(m / 60), r = m % 60;
  return h ? `${h} h${r ? " " + String(r).padStart(2, "0") : ""}` : `${r} min`;
}

/** Valeur affichée dans le champ « Temps » (ex. « 6h30 »). */
export const hoursField = (minutes: number | null | undefined): string => fmtHours(minutes).replace(" h ", "h").replace(" h", "h");

export interface WeekMaxForm { hours: number | null; km: number | null; dplus: number | null }

/** Contrôle du formulaire : message d'erreur, ou null si tout va bien. */
export function weekMaxError(f: WeekMaxForm): string | null {
  if ([f.hours, f.km, f.dplus].some((x) => Number.isNaN(x))) return "Je n'ai pas compris une des valeurs (ex. 6h30, 50, 1500).";
  if (f.hours !== null && (f.hours < 1 || f.hours > 40)) return "Le temps doit être entre 1 h et 40 h par semaine.";
  if (f.km !== null && (f.km < 5 || f.km > 400)) return "La distance doit être entre 5 et 400 km par semaine.";
  if (f.dplus !== null && (f.dplus < 100 || f.dplus > 20000)) return "Le D+ doit être entre 100 et 20 000 m par semaine.";
  return null;
}
