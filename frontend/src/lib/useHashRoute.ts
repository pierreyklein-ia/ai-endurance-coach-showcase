/* Navigation par l'ancre de l'URL (#aujourdhui, #coach…), sans bibliothèque de routage.
   `nonce` change à chaque navigation, même vers la vue déjà affichée : la vue se recharge (comme go() avant). */
import { useCallback, useEffect, useRef, useState } from "react";

/** v0.39 : les réglages (ex-onglet Détails) sont dans la page Carrière ; #details y mène toujours. */
const ALIASES: Record<string, string> = { details: "carriere", reglages: "carriere" };
export const hashView = (): string => { const v = location.hash.slice(1) || "aujourdhui"; return ALIASES[v] || v; };

export function useHashRoute(): { view: string; nonce: number; go: (view: string) => void; viewRef: { current: string } } {
  const [nav, setNav] = useState({ view: hashView(), nonce: 0 });
  const viewRef = useRef(nav.view);
  const go = useCallback((view: string) => {
    viewRef.current = view;
    setNav((n) => ({ view, nonce: n.nonce + 1 }));
  }, []);
  useEffect(() => {
    const on = () => go(hashView());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [go]);
  return { view: nav.view, nonce: nav.nonce, go, viewRef };
}
