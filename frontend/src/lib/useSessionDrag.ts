/* v0.37 : geste de glisser-déposer d'une séance (Pointer Events).
   - souris / stylet de bureau : le glisser commence après quelques pixels ;
   - doigt : appui long (LONG_PRESS_MS) sans bouger, sinon c'est un défilement normal de la page.
   Le glisser HTML5 natif ne marche pas au doigt sur iOS / Android : tout est fait à la main
   (fantôme qui suit le doigt, cibles = cartes des jours [data-drop-day], défilement près des bords). */
import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent, type RefObject } from "react";
import { edgeScroll, LONG_PRESS_MS, MOUSE_THRESHOLD, MOVE_TOLERANCE, pickDay, type DayRect } from "./drag";

export interface DragGhost { id: string; title: string; meta: string; x: number; y: number; w: number; offX: number; offY: number }

interface Gesture {
  id: string; title: string; meta: string; pointerId: number; mouse: boolean;
  x0: number; y0: number; x: number; y: number;
  el: HTMLElement; timer?: ReturnType<typeof setTimeout>; raf?: number; active: boolean;
}

export function useSessionDrag(container: RefObject<HTMLElement | null>, onDrop: (id: string, day: string) => void) {
  const [ghost, setGhost] = useState<DragGhost | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const g = useRef<Gesture | null>(null);
  const dropRef = useRef(onDrop);
  dropRef.current = onDrop;
  const suppress = useRef(false);

  const measure = useCallback((): DayRect[] => {
    const root = container.current;
    if (!root) return [];
    return Array.from(root.querySelectorAll<HTMLElement>("[data-drop-day]")).map((el) => {
      const r = el.getBoundingClientRect();
      return { day: el.dataset.dropDay || "", top: r.top, bottom: r.bottom, left: r.left, right: r.right };
    });
  }, [container]);

  const insets = (): [number, number] => {
    const top = document.querySelector(".topbar")?.getBoundingClientRect().bottom ?? 0;
    const tabs = document.querySelector(".tabs")?.getBoundingClientRect();
    return [Math.max(0, top), tabs && tabs.height ? Math.max(0, window.innerHeight - tabs.top) : 0];
  };

  const follow = useCallback(() => {
    const s = g.current;
    if (!s) return;
    setOver(pickDay(measure(), s.x, s.y));
    setGhost((gh) => (gh ? { ...gh, x: s.x, y: s.y } : gh));
  }, [measure]);

  const listeners = useRef<{ move: (e: PointerEvent) => void; up: (e: PointerEvent) => void;
    cancel: (e: PointerEvent) => void; key: (e: KeyboardEvent) => void } | null>(null);

  const finish = useCallback((drop: boolean) => {
    const s = g.current;
    if (!s) return;
    clearTimeout(s.timer);
    if (s.raf) cancelAnimationFrame(s.raf);
    const l = listeners.current;
    if (l) {
      window.removeEventListener("pointermove", l.move);
      window.removeEventListener("pointerup", l.up);
      window.removeEventListener("pointercancel", l.cancel);
      window.removeEventListener("keydown", l.key);
    }
    g.current = null;
    document.body.classList.remove("dragging");
    if (s.active) {
      suppress.current = true;                         // le « click » qui suit le relâcher n'ouvre pas la séance
      setTimeout(() => { suppress.current = false; }, 50);
      const day = drop ? pickDay(measure(), s.x, s.y) : null;
      setGhost(null); setOver(null);
      if (day) dropRef.current(s.id, day);
    }
  }, [measure]);

  const activate = useCallback(() => {
    const s = g.current;
    if (!s || s.active) return;
    s.active = true;
    const r = s.el.getBoundingClientRect();
    setGhost({ id: s.id, title: s.title, meta: s.meta, x: s.x, y: s.y, w: r.width, offX: s.x - r.left, offY: s.y - r.top });
    setOver(pickDay(measure(), s.x, s.y));
    document.body.classList.add("dragging");
    try { navigator.vibrate?.(12); } catch { /* pas de vibreur */ }
    const loop = () => {
      const cur = g.current;
      if (!cur || !cur.active) return;
      const [top, bottom] = insets();
      const dy = edgeScroll(cur.y, window.innerHeight, top, bottom);
      if (dy) { window.scrollBy(0, dy); follow(); }
      cur.raf = requestAnimationFrame(loop);
    };
    s.raf = requestAnimationFrame(loop);
  }, [follow, measure]);

  const onPointerDown = useCallback((e: RPointerEvent<HTMLElement>, id: string, title: string, meta: string) => {
    if (g.current) return;
    const mouse = e.pointerType === "mouse";
    if (mouse && e.button !== 0) return;
    if ((e.target as HTMLElement).closest("button, a, input")) return;      // bouton « Déplacer », liens
    g.current = { id, title, meta, pointerId: e.pointerId, mouse, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY,
      el: e.currentTarget, active: false };
    if (!mouse) g.current.timer = setTimeout(activate, LONG_PRESS_MS);
    const move = (ev: PointerEvent) => {
      const s = g.current;
      if (!s || ev.pointerId !== s.pointerId) return;
      s.x = ev.clientX; s.y = ev.clientY;
      if (!s.active) {
        const d = Math.hypot(s.x - s.x0, s.y - s.y0);
        if (s.mouse ? d > MOUSE_THRESHOLD : d > MOVE_TOLERANCE) {
          if (s.mouse) activate(); else finish(false);           // au doigt : il fait défiler la page
        }
        if (!g.current?.active) return;
      }
      ev.preventDefault();
      follow();
    };
    const up = (ev: PointerEvent) => { if (g.current && ev.pointerId === g.current.pointerId) finish(true); };
    const cancel = (ev: PointerEvent) => { if (g.current && ev.pointerId === g.current.pointerId) finish(false); };
    const key = (ev: KeyboardEvent) => { if (ev.key === "Escape") finish(false); };
    listeners.current = { move, up, cancel, key };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key);
  }, [activate, finish, follow]);

  // Au doigt, une fois la séance saisie, la page ne doit plus défiler : écouteur NON passif posé
  // dès le départ (Chrome décide au toucher s'il peut défiler sans attendre la page).
  useEffect(() => {
    const root = container.current;
    if (!root) return;
    const tm = (e: TouchEvent) => { if (g.current?.active && e.cancelable) e.preventDefault(); };
    const cm = (e: Event) => { if (g.current) e.preventDefault(); };   // pas de menu contextuel sur l'appui long
    root.addEventListener("touchmove", tm, { passive: false });
    root.addEventListener("contextmenu", cm);
    return () => { root.removeEventListener("touchmove", tm); root.removeEventListener("contextmenu", cm); };
  }, [container]);

  useEffect(() => () => finish(false), [finish]);

  /** À appeler dans le onClick de la séance : vrai si ce clic termine un glisser (à ignorer). */
  const consumeClick = useCallback(() => suppress.current, []);

  return { ghost, over, onPointerDown, consumeClick };
}
