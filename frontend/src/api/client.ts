/* Appels à l'API : cookie de session, JSON, et retour à l'écran de connexion sur 401. */
export interface ApiOptions extends Omit<RequestInit, "body" | "headers"> {
  json?: unknown;
  body?: BodyInit | null;
  headers?: Record<string, string>;
}

/** Erreur HTTP de l'API (le serveur a répondu) ; une coupure réseau lève une TypeError de fetch. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

let unauthorized: () => void = () => {};
/** L'application enregistre ici ce qu'il faut faire quand la session a expiré (afficher la connexion). */
export function onUnauthorized(fn: () => void): void { unauthorized = fn; }

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { json, ...rest } = opts;
  const init: RequestInit & { headers: Record<string, string> } = { credentials: "same-origin", ...rest, headers: { ...(opts.headers || {}) } };
  if (json !== undefined) {
    init.method = init.method || "POST";
    init.body = JSON.stringify(json);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (r.status === 401 && path !== "/api/login") { unauthorized(); throw new Error("connexion requise"); }
  const data = (await r.json().catch(() => ({}))) as { error?: string };
  if (!r.ok) throw new ApiError(data.error || `erreur ${r.status}`, r.status);
  return data as T;
}
