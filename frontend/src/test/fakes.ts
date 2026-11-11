/* Outils de test partagés : faux `fetch` (routes -> réponses JSON) et objets du domaine prêts à l'emploi.
   Inversion de contrôle côté front : les composants appellent `api()` qui appelle `fetch` ; on remplace `fetch`
   (dépendance globale) par une fonction de test, sans toucher au code des composants. */
import { vi } from "vitest";
import type { Career, Session } from "../api/types";

export interface Call { method: string; path: string; body: unknown }
type Handler = unknown | ((body: unknown, call: Call) => unknown);
/** Réponse avec un code HTTP : `reply(422, { error: "…" })`. */
export class Reply { constructor(readonly status: number, readonly json: unknown) {} }
export const reply = (status: number, json: unknown): Reply => new Reply(status, json);

/** Remplace `fetch` : clé « MÉTHODE /chemin » (la plus longue qui préfixe l'adresse gagne). Retourne les appels. */
export function stubFetch(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const path = String(url);
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body ?? null;
    const call = { method, path, body };
    calls.push(call);
    const key = Object.keys(routes).filter((k) => { const [m, p] = k.split(" "); return m === method && path.startsWith(p); })
      .sort((a, b) => b.length - a.length)[0];
    let out: unknown = key === undefined ? reply(404, { error: "route inconnue" }) : routes[key];
    if (typeof out === "function") out = (out as (b: unknown, c: Call) => unknown)(body, call);
    const r = out instanceof Reply ? out : reply(200, out);
    return { ok: r.status < 400, status: r.status, json: async () => r.json } as Response;
  }));
  return calls;
}

export function session(o: Partial<Session> = {}): Session {
  return { session_id: "s1", day: "2026-11-05", intensity: "threshold", kind: "seuil", title: "Seuil 3 × 10 min",
    detail: "Échauffement 20 min puis 3 × 10 min au seuil.", duration_min: 70, elevation_gain_m: 0, key: true,
    feedback: null, ...o };
}

export function career(o: Partial<Career> = {}): Career {
  return { level: 7, rank: "Confirmé", xp: 2602, next_at: 3000, pct: 64, trophies: [], families: {}, timeline: [], levels: [],
    gear: [], scene: "route", count: 3, total: 40, next_levels: [], new: [], level_up: false, first_visit: false,
    avatar: { skin: 1, hair: "court", hair_color: 0, beard: false, shirt: 0, shorts: 0, shoes: 0, gear: [], scene: null },
    avatar_set: true, discipline: "trail", ...o };
}

/** Laisse passer les promesses en attente (réponses du faux fetch). */
export const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));
