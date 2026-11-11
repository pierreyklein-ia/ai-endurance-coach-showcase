/* Glisser-déposer d'une séance (souris et appui long au doigt), sans navigateur réel : les positions des cartes des
   jours sont fixées (getBoundingClientRect remplacé) et les évènements de pointeur sont envoyés à la main. */
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { LONG_PRESS_MS } from "./drag";
import { useSessionDrag } from "./useSessionDrag";

type Api = ReturnType<typeof useSessionDrag>;
let api: Api;

function Harness({ onDrop }: { onDrop: (id: string, day: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  api = useSessionDrag(ref, onDrop);
  return (
    <div ref={ref}>
      <div data-drop-day="2026-11-05" id="d1"><div id="card">Seuil</div></div>
      <div data-drop-day="2026-11-06" id="d2"><button id="btn">Déplacer</button></div>
    </div>
  );
}

const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 300, width: 300, height: bottom - top, x: 0, y: top,
  toJSON: () => ({}) }) as DOMRect;

function pev(type: string, x: number, y: number, pointerId = 1): Event {
  const e = new MouseEvent(type, { clientX: x, clientY: y, bubbles: true, cancelable: true });
  Object.defineProperty(e, "pointerId", { value: pointerId });
  return e;
}

function down(target: string, x: number, y: number, pointerType = "mouse", button = 0) {
  const el = document.getElementById(target)!;
  act(() => api.onPointerDown({ pointerType, button, target: el, currentTarget: document.getElementById("d1")!, pointerId: 1,
    clientX: x, clientY: y } as unknown as Parameters<Api["onPointerDown"]>[0], "s1", "Seuil", "70 min"));
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.id === "d2" ? rect(200, 400) : rect(0, 200);
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  window.scrollBy = vi.fn() as unknown as typeof window.scrollBy;
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("glisser-déposer d'une séance", () => {
  it("souris : le glisser commence après quelques pixels et dépose sur le jour survolé", () => {
    vi.useFakeTimers();
    const onDrop = vi.fn();
    render(<Harness onDrop={onDrop} />);
    down("card", 50, 50);
    act(() => { window.dispatchEvent(pev("pointermove", 51, 51)); });
    expect(api.ghost).toBeNull();                                   // sous le seuil
    act(() => { window.dispatchEvent(pev("pointermove", 60, 120)); });
    expect(api.ghost).toMatchObject({ id: "s1", title: "Seuil" });
    expect(document.body.classList.contains("dragging")).toBe(true);
    act(() => { window.dispatchEvent(pev("pointermove", 60, 300)); });
    expect(api.over).toBe("2026-11-06");
    act(() => { window.dispatchEvent(pev("pointermove", 60, 300, 2)); });   // autre pointeur : ignoré
    act(() => { window.dispatchEvent(pev("pointerup", 60, 300)); });
    expect(onDrop).toHaveBeenCalledWith("s1", "2026-11-06");
    expect(api.ghost).toBeNull();
    expect(api.consumeClick()).toBe(true);                          // le clic qui suit est ignoré
    act(() => { vi.advanceTimersByTime(60); });
    expect(api.consumeClick()).toBe(false);
  });

  it("doigt : appui long pour saisir ; bouger avant, c'est faire défiler la page", () => {
    vi.useFakeTimers();
    const onDrop = vi.fn();
    render(<Harness onDrop={onDrop} />);
    down("card", 50, 50, "touch");
    act(() => { window.dispatchEvent(pev("pointermove", 50, 80)); });   // défilement : abandon
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS + 10); });
    expect(api.ghost).toBeNull();
    down("card", 50, 50, "touch");
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS + 10); });
    expect(api.ghost).not.toBeNull();
    act(() => { window.dispatchEvent(pev("pointermove", 50, 250)); });
    act(() => { window.dispatchEvent(pev("pointercancel", 50, 250)); });  // annulé par le système : pas de dépôt
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("Échap annule ; bouton droit et boutons internes ne déclenchent rien", () => {
    const onDrop = vi.fn();
    render(<Harness onDrop={onDrop} />);
    down("card", 50, 50, "mouse", 2);
    down("btn", 50, 250);
    act(() => { window.dispatchEvent(pev("pointermove", 80, 300)); });
    expect(api.ghost).toBeNull();
    down("card", 50, 50);
    act(() => { window.dispatchEvent(pev("pointermove", 80, 300)); });
    expect(api.ghost).not.toBeNull();
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(api.ghost).toBeNull();
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("hors de toute carte : pas de dépôt ; menu contextuel bloqué pendant la saisie", () => {
    const onDrop = vi.fn();
    const { container } = render(<Harness onDrop={onDrop} />);
    down("card", 50, 50);
    const ctx = new Event("contextmenu", { cancelable: true });
    container.firstElementChild!.dispatchEvent(ctx);
    expect(ctx.defaultPrevented).toBe(true);
    act(() => { window.dispatchEvent(pev("pointermove", 500, 900)); });
    act(() => { window.dispatchEvent(pev("pointerup", 500, 900)); });
    expect(onDrop).not.toHaveBeenCalled();
  });
});
