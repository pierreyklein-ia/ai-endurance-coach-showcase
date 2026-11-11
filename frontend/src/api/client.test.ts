import { api, onUnauthorized } from "./client";

const reply = (status: number, body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("api()", () => {
  afterEach(() => { vi.unstubAllGlobals(); onUnauthorized(() => {}); });

  it("envoie le JSON en POST avec le cookie de session", async () => {
    const f = reply(200, { ok: true });
    vi.stubGlobal("fetch", f);
    await expect(api("/api/chat", { json: { text: "salut" } })).resolves.toEqual({ ok: true });
    const [path, init] = f.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }];
    expect(path).toBe("/api/chat");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe('{"text":"salut"}');
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("401 : affiche la connexion et rejette", async () => {
    vi.stubGlobal("fetch", reply(401, { error: "connexion requise" }));
    const login = vi.fn();
    onUnauthorized(login);
    await expect(api("/api/today")).rejects.toThrow("connexion requise");
    expect(login).toHaveBeenCalledOnce();
  });

  it("un mauvais code sur /api/login ne renvoie pas à l'écran de connexion : il montre l'erreur", async () => {
    vi.stubGlobal("fetch", reply(401, { error: "code incorrect" }));
    const login = vi.fn();
    onUnauthorized(login);
    await expect(api("/api/login", { json: { passcode: "x" } })).rejects.toThrow("code incorrect");
    expect(login).not.toHaveBeenCalled();
  });

  it("erreur sans message : « erreur <code> »", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("pas du json", { status: 502 })));
    await expect(api("/api/sync", { method: "POST" })).rejects.toThrow("erreur 502");
  });
});
