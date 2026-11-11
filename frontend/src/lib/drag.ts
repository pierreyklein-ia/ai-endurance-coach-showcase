/* v0.37 : glisser-déposer des séances dans Semaine — règles et géométrie (fonctions pures, testées).
   Le geste lui-même (Pointer Events, appui long au doigt) est dans useSessionDrag.ts. */
import type { Session, WeekView } from "../api/types";

export const LONG_PRESS_MS = 350;      // appui long au doigt avant de saisir la séance
export const MOVE_TOLERANCE = 8;       // px : au-delà, avant l'appui long, c'est un défilement
export const MOUSE_THRESHOLD = 5;      // px : à la souris, le glisser commence dès ce déplacement

export interface DayRect { day: string; top: number; bottom: number; left: number; right: number }

/** Séance saisissable : à venir (ou du jour), pas validée, ni course ni repos (v0.44 : le renfo, oui). */
export function canDrag(s: Session, _today?: string): boolean {
  const renfo = s.kind === "renfo" || s.kind === "mobilite";
  // v0.45 : une séance passée non validée (manquée) se replanifie aussi, vers aujourd'hui ou plus tard
  return !s.is_race && s.intensity !== "race" && (s.intensity !== "rest" || renfo) && !s.feedback;
}

/** Jours de course de la semaine affichée (aucune autre séance ces jours-là). */
export function raceDays(w: WeekView): Set<string> {
  return new Set(w.days.filter((d) => d.sessions.some((s) => s.is_race || s.intensity === "race")).map((d) => d.date));
}

/** Pourquoi on ne peut pas déposer ici (null = autorisé). Même règles que le serveur, pour l'affichage.
    v0.38 : seul l'impossible est refusé ; un jour de course devient un avertissement (`dropWarning`). */
export function dropRefusal(s: Session, day: string, today: string, _races?: Set<string>): string | null {
  if (day === s.day) return "même jour";
  if (day < today) return "jour passé";
  return null;
}

/** v0.38 : séance dure, même définition que le serveur (guardrails.HARD_INTENSITIES, hors course et charge nulle). */
export const HARD_INTENSITIES = new Set(["tempo", "threshold", "vo2", "hills", "long"]);
export function isHard(s: Session): boolean {
  return HARD_INTENSITIES.has(s.intensity) && !s.is_race && (s.planned_load ?? 1) > 0;
}

/** Avertissement affiché sur le jour visé pendant le glisser (null = rien à signaler). */
export function dropWarning(s: Session, day: WeekView["days"][number], races: Set<string>): { kind: "race" | "two_hard"; label: string } | null {
  if (day.date === s.day) return null;
  if (races.has(day.date)) return { kind: "race", label: "jour de course" };
  if (isHard(s) && day.sessions.some((x) => x.session_id !== s.session_id && isHard(x))) return { kind: "two_hard", label: "2 séances dures le même jour" };
  return null;
}

/** Jour sous le doigt : la carte qui contient le point, sinon la plus proche verticalement (tolérance 24 px). */
export function pickDay(rects: DayRect[], x: number, y: number): string | null {
  const inside = rects.find((r) => y >= r.top && y <= r.bottom && x >= r.left && x <= r.right);
  if (inside) return inside.day;
  let best: DayRect | null = null, dist = 24.5;
  for (const r of rects) {
    if (x < r.left || x > r.right) continue;
    const d = y < r.top ? r.top - y : y - r.bottom;
    if (d < dist) { dist = d; best = r; }
  }
  return best ? best.day : null;
}

/** Défilement automatique près des bords (px par image, négatif = vers le haut). */
export function edgeScroll(y: number, viewportH: number, topInset = 0, bottomInset = 0, edge = 72, max = 18): number {
  const top = y - topInset, bottom = viewportH - bottomInset - y;
  if (top < edge) return -Math.ceil(max * Math.min(1, (edge - Math.max(top, 0)) / edge));
  if (bottom < edge) return Math.ceil(max * Math.min(1, (edge - Math.max(bottom, 0)) / edge));
  return 0;
}

/** Mise à jour optimiste : la séance passe au jour visé (le serveur fait foi ensuite). */
export function moveLocal(w: WeekView, sessionId: string, day: string): WeekView {
  const s = w.days.flatMap((d) => d.sessions).find((x) => x.session_id === sessionId);
  if (!s || s.day === day || !w.days.some((d) => d.date === day)) return w;
  const moved = { ...s, day };
  return {
    ...w,
    days: w.days.map((d) => ({
      ...d,
      sessions: d.date === day ? [...d.sessions.filter((x) => x.intensity !== "rest"), moved]
        : d.date === s.day ? d.sessions.filter((x) => x.session_id !== sessionId) : d.sessions,
    })),
  };
}
