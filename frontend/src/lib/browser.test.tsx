/* Aides liées au navigateur : notifications, navigation par l'ancre, bouton « retour », stockage local, formats. */
import { act, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { reply, stubFetch } from "../test/fakes";
import { afterBack, openOverlays, useBackClose } from "./backClose";
import { fmtDur, fmtPace, fr1, frDateTime, joinBase, lsGet, lsSet, niceTicks, signed, timeOf } from "./format";
import { b64ToBytes, enablePush } from "./push";
import { onToast } from "./toast";
import { hashView, useHashRoute } from "./useHashRoute";

const w = window as unknown as Record<string, unknown>;
const nav = navigator as unknown as Record<string, unknown>;
afterEach(() => { vi.unstubAllGlobals(); onToast(null); delete w.PushManager; delete nav.serviceWorker; location.hash = ""; });

describe("notifications", () => {
  it("décode la clé publique VAPID (base64url)", () => {
    expect(Array.from(b64ToBytes("AQID"))).toEqual([1, 2, 3]);
    expect(Array.from(b64ToBytes("-_8"))).toEqual([251, 255]);
  });

  function pushEnv(perm: string) {
    const subscribe = vi.fn(async () => ({ toJSON: () => ({ endpoint: "https://push/1", keys: {} }) }));
    nav.serviceWorker = { ready: Promise.resolve({ pushManager: { subscribe } }) };
    w.PushManager = function PushManager() {};
    vi.stubGlobal("Notification", { requestPermission: vi.fn(async () => perm) });
    return subscribe;
  }

  it("iPhone hors écran d'accueil : explication", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    await enablePush("AQID");
    expect(toasts[0]).toContain("écran d'accueil");
  });

  it("refus, succès, échec", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    pushEnv("denied");
    await enablePush("AQID");
    expect(toasts.at(-1)).toBe("Notifications refusées dans les réglages.");
    const subscribe = pushEnv("granted");
    const calls = stubFetch({ "POST /api/push/subscribe": { ok: true }, "POST /api/push/test": { sent: 1 } });
    await enablePush("AQID");
    expect(subscribe).toHaveBeenCalled();
    expect(calls.map((c) => c.path)).toEqual(["/api/push/subscribe", "/api/push/test"]);
    expect(toasts.at(-1)).toBe("Notifications activées ✓");
    pushEnv("granted");
    stubFetch({ "POST /api/push/subscribe": reply(503, { error: "non configurées" }) });
    await enablePush("AQID");
    expect(toasts.at(-1)).toBe("Activation impossible : non configurées");
  });
});

describe("navigation par l'ancre", () => {
  it("vue par défaut, alias des réglages, rechargement à chaque navigation", () => {
    expect(hashView()).toBe("aujourdhui");
    location.hash = "#reglages";
    expect(hashView()).toBe("carriere");
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.view).toBe("carriere");
    act(() => result.current.go("carriere"));
    expect(result.current.nonce).toBe(1);
    act(() => { location.hash = "#coach"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(result.current.view).toBe("coach");
    expect(result.current.viewRef.current).toBe("coach");
  });
});

function Overlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  useBackClose(open, onClose);
  return null;
}

describe("bouton « retour » (Android)", () => {
  it("« retour » ferme la fiche ouverte ; fermée autrement, elle retire son entrée d'historique", async () => {
    const onClose = vi.fn();
    const back = vi.spyOn(history, "back").mockImplementation(() => { window.dispatchEvent(new PopStateEvent("popstate")); });
    const { rerender } = render(<Overlay open onClose={onClose} />);
    expect(openOverlays()).toBe(1);
    act(() => { window.dispatchEvent(new PopStateEvent("popstate")); });     // « retour »
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(openOverlays()).toBe(0);
    rerender(<Overlay open={false} onClose={onClose} />);
    rerender(<Overlay open onClose={onClose} />);
    expect(openOverlays()).toBe(1);
    const after = vi.fn();
    rerender(<Overlay open={false} onClose={onClose} />);                     // fermée par ✕ : history.back()
    expect(back).toHaveBeenCalled();
    expect(openOverlays()).toBe(0);
    afterBack(after);
    expect(after).toHaveBeenCalled();
    back.mockRestore();
  });

  it("afterBack attend la fin du retour en arrière en cours", () => {
    const pending: (() => void)[] = [];
    const back = vi.spyOn(history, "back").mockImplementation(() => { pending.push(() => window.dispatchEvent(new PopStateEvent("popstate"))); });
    const { rerender } = render(<Overlay open onClose={() => {}} />);
    rerender(<Overlay open={false} onClose={() => {}} />);
    const fn = vi.fn();
    afterBack(fn);
    expect(fn).not.toHaveBeenCalled();
    pending.forEach((p) => p());
    expect(fn).toHaveBeenCalled();
    back.mockRestore();
  });
});

describe("formats et stockage", () => {
  it("durées, allures, signes, heures, graduations", () => {
    expect(fmtDur(null)).toBe("n/d");
    expect(fmtDur(3725)).toBe("1 h 02");
    expect(fmtDur(125)).toBe("2:05");
    expect(fmtPace(330)).toBe("5:30/km");
    expect(fr1(undefined)).toBe("n/d");
    expect(fr1(3.14)).toBe("3,1");
    expect(signed(null)).toBe("n/d");
    expect(signed(3)).toBe("+3");
    expect(signed(-2)).toBe("-2");
    expect(timeOf("2026-11-05T18:05:00")).toMatch(/18:05/);
    expect(frDateTime("2026-11-05T08:07:00")).toBe("5/11 à 08 h 07");
    expect(niceTicks(0, 100)).toEqual([0, 25, 50, 75, 100]);
    expect(niceTicks(5, 5).length).toBeGreaterThan(0);
    expect(joinBase("Bonjour.  ", " ça va")).toBe("Bonjour. ça va");
    expect(joinBase("", " seul ")).toBe("seul");
  });

  it("stockage local indisponible (navigation privée) : sans erreur", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("privé"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("privé"); });
    expect(lsGet("x")).toBeNull();
    expect(() => lsSet("x", "1")).not.toThrow();
    get.mockRestore(); set.mockRestore();
  });
});
