/* v0.50 : « Récap de la semaine » (vue Semaine) : quel récap montrer (fonctions pures, testées). */
import type { RecapItem, RecapView, WeekFeeling } from "../api/types";

/** Le dernier récap posté s'il couvre la semaine affichée ou la précédente, sinon l'aperçu de la semaine en cours. */
export function recapToShow(v: RecapView | null, weekStart: string): { item: RecapItem; preview: boolean } | null {
  if (!v) return null;
  if (v.last && (v.last.week_start >= addDaysIso(weekStart, -7))) return { item: v.last, preview: false };
  if (v.preview) return { item: v.preview, preview: true };
  if (v.last) return { item: v.last, preview: false };
  return null;
}

export function addDaysIso(iso: string, n: number): string {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Ligne à mettre en avant (alerte ou question) : « ⚠ … » / « ❓ … ». */
export const isAlert = (line: string): boolean => /^(⚠|❓|🏁)/.test(line);

/** v0.51 : la vue du récap avec le ressenti enregistré pour la semaine `weekStart`. */
export function withFeeling(v: RecapView | null, weekStart: string, f: WeekFeeling): RecapView | null {
  if (!v) return v;
  const put = (it: RecapItem | null): RecapItem | null => (it && it.week_start === weekStart ? { ...it, feeling: f } : it);
  return { last: put(v.last), preview: put(v.preview) };
}
