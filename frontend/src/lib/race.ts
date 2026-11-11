/* v0.38 : simulateur de course — fonctions pures (formats, temps visé, profil SVG), testées. */
import type { ScenarioId, SimCutoff, SimPoint } from "../api/types";

/** « 9 h 15 » (minutes arrondies) */
export function fmtHM(s: number): string {
  const m = Math.round(s / 60);
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

/** Durée courte d'un tronçon : « 32 min », « 1 h 05 » */
export function fmtLeg(s: number): string {
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : fmtHM(s);
}

/** Allure « 6:12/km » */
export function fmtPaceKm(s: number | null | undefined): string {
  if (!s) return "—";
  const r = Math.round(s);
  return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}/km`;
}

/** Marge sur une barrière : « +3 h 02 », « +45 min », « −12 min » */
export function fmtMargin(min: number): string {
  const sign = min < 0 ? "−" : "+";
  const a = Math.abs(Math.round(min));
  return a >= 60 ? `${sign}${Math.floor(a / 60)} h ${String(a % 60).padStart(2, "0")}` : `${sign}${a} min`;
}

export const STATUS_LABEL: Record<SimCutoff["status"], string> = { ok: "large", juste: "juste", danger: "danger" };

/** « 9:00 », « 9h00 », « 9 h », « 540 » (minutes) -> minutes ; null si illisible ou hors 0 h 30 – 72 h. */
export function parseTarget(x: string): number | null {
  const s = x.trim().toLowerCase().replace(/\s+/g, "");
  let m: number | null = null;
  const hm = /^(\d{1,2})[:h](\d{0,2})$/.exec(s);
  if (hm) {
    const mm = hm[2] ? Number(hm[2]) : 0;
    if (mm >= 60) return null;
    m = Number(hm[1]) * 60 + mm;
  } else if (/^\d+$/.test(s)) m = Number(s);
  return m !== null && m >= 30 && m <= 72 * 60 ? m : null;
}

/** minutes -> « 9:05 » (champ du temps visé) */
export const targetText = (min: number): string => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")}`;

export interface ProfileGeom {
  W: number; H: number; L: number; R: number; T: number; B: number;
  line: string; area: string; x: (km: number) => number; y: (alt: number) => number;
  yTicks: number[]; xTicks: number[]; altAt: (km: number) => number;
  /** v0.39 : abscisse SVG -> km (survol du profil), bornée au parcours */
  kmAt: (x: number) => number;
}

/** Géométrie du profil d'altitude (viewBox fixe, la largeur suit le conteneur). */
export function profileGeom(pts: [number, number][], W = 640, H = 200): ProfileGeom {
  const L = 40, R = 8, T = 18, B = 22;
  const kms = pts.map((p) => p[0]), alts = pts.map((p) => p[1]);
  const k1 = Math.max(...kms, 1), a0 = Math.min(...alts), a1 = Math.max(...alts);
  const lo = Math.floor((a0 - 40) / 100) * 100, hi = Math.ceil((a1 + 60) / 100) * 100;
  const x = (km: number) => L + (km / k1) * (W - L - R);
  const y = (alt: number) => H - B - ((alt - lo) / (hi - lo || 1)) * (H - T - B);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(" ");
  const area = `${line} L${x(kms[kms.length - 1]).toFixed(1)} ${H - B} L${x(kms[0]).toFixed(1)} ${H - B} Z`;
  const step = hi - lo > 1200 ? 500 : hi - lo > 500 ? 250 : 100;
  const yTicks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) yTicks.push(v);
  const xs = k1 > 120 ? 20 : k1 > 40 ? 10 : 5;
  const xTicks: number[] = [];
  for (let v = 0; v <= k1 + 1e-6; v += xs) xTicks.push(v);
  const altAt = (km: number) => {
    let j = kms.findIndex((k) => k >= km);
    if (j <= 0) return alts[Math.max(j, 0)];
    const t = (km - kms[j - 1]) / (kms[j] - kms[j - 1] || 1);
    return alts[j - 1] + t * (alts[j] - alts[j - 1]);
  };
  const kmAt = (sx: number) => Math.min(k1, Math.max(0, ((sx - L) / (W - L - R)) * k1));
  return { W, H, L, R, T, B, line, area, x, y, yTicks, xTicks, altAt, kmAt };
}

/** Points montrés sur le profil : ravitos, points d'eau, sommets/repères avec barrière ; pas le départ ni l'arrivée. */
export const profileMarks = (pts: SimPoint[]): SimPoint[] => pts.filter((p) => p.kind === "ravito" || p.kind === "eau" || !!p.cutoff && p.kind !== "arrivee");

/** Lignes du résumé « plan de course » à copier/partager (une par ravito). */
export function shareText(name: string, headline: string, lines: string[]): string {
  return [name, headline, "", ...lines].join("\n");
}

/** v0.39 : « 06:00 » + 4 h 02 -> « 10:02 » (au-delà de minuit, l'heure repart de 0). */
export function clockAdd(start: string, s: number): string {
  const [h, m] = start.split(":").map(Number);
  const t = Math.round((h * 60 + m) + s / 60);
  return `${String(Math.floor(t / 60) % 24).padStart(2, "0")}:${String(((t % 60) + 60) % 60).padStart(2, "0")}`;
}

/** v0.39 : temps écoulé (s) au kilomètre `km` pour un scénario, interpolé entre deux points de passage ;
    on repart d'un point après son arrêt (stop_min). */
export function elapsedAtKm(pts: SimPoint[], sc: ScenarioId, km: number): number {
  if (!pts.length) return 0;
  if (km <= pts[0].km) return 0;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    if (km <= b.km) {
      const leave = a.arrival[sc].s + (i > 1 ? a.stop_min * 60 : 0);
      const t = (km - a.km) / (b.km - a.km || 1);
      return leave + t * Math.max(0, b.arrival[sc].s - leave);
    }
  }
  return pts[pts.length - 1].arrival[sc].s;
}
