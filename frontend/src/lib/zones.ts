/* v0.50 : « Mes zones » et FC cible des séances : affichage et contrôle de la saisie (fonctions pures, testées). */
import type { SessionTarget, ZoneValue } from "../api/types";

/** Ligne courte de la carte de séance : « FC 150-160 bpm · 4:00/km » (ou allure d'abord sur fractions courtes). */
export function targetLine(t: SessionTarget | null | undefined): string {
  if (!t) return "";
  const hr = `FC ${t.hr_text}`;
  if (t.lead === "pace" && t.pace) return `${t.pace} · effort ${t.rpe} · ${hr} en fin de bloc`;
  if (t.lead === "effort") return [`effort ${t.rpe}`, hr, t.pace].filter(Boolean).join(" · ");
  return [hr, t.pace].filter(Boolean).join(" · ");
}

/** Pastille de source : « saisie », « Strava », « estimée », « par défaut ». */
export function sourceChip(v: ZoneValue): string {
  return v.source_id === "reglage" ? "par défaut" : v.source;
}

export interface ZonesForm { hr_max: number | null; hr_rest: number | null; lthr: number | null }

/** « 172 », « 172 bpm », vide -> null ; illisible -> NaN. */
export function parseBpm(raw: string): number | null {
  const t = raw.trim().toLowerCase().replace(/\s*bpm$/, "");
  if (!t) return null;
  const x = Number(t.replace(",", "."));
  return Number.isFinite(x) ? Math.round(x) : NaN;
}

/** Contrôle de la saisie : message d'erreur, ou null. `cur` : valeurs actuelles pour les champs laissés vides. */
export function zonesError(f: ZonesForm, cur: { hr_max: number; hr_rest: number; lthr: number }): string | null {
  if ([f.hr_max, f.hr_rest, f.lthr].some((x) => Number.isNaN(x))) return "Je n'ai pas compris une des valeurs (ex. 185).";
  if (f.hr_max !== null && (f.hr_max < 120 || f.hr_max > 230)) return "La FC max doit être entre 120 et 230 bpm.";
  if (f.hr_rest !== null && (f.hr_rest < 25 || f.hr_rest > 110)) return "La FC de repos doit être entre 25 et 110 bpm.";
  if (f.lthr !== null && (f.lthr < 100 || f.lthr > 220)) return "La FC au seuil doit être entre 100 et 220 bpm.";
  const mx = f.hr_max ?? cur.hr_max, rest = f.hr_rest ?? cur.hr_rest, lt = f.lthr ?? cur.lthr;
  if (!(rest < lt && lt < mx)) return "Il faut FC de repos < FC au seuil < FC max.";
  return null;
}

/** Plage d'une zone : « < 145 », « 145-152 », « ≥ 171 ». */
export function zoneRange(lo: number | null, hi: number | null): string {
  if (lo === null && hi !== null) return `< ${hi + 1}`;
  if (hi === null && lo !== null) return `≥ ${lo}`;
  return `${lo}-${hi}`;
}
