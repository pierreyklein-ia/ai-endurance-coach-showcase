/* v0.48 : le bouton « retour » du téléphone (Android) ferme d'abord la fiche ouverte (détail d'une séance, d'une
   activité, feuille de retour, éditeur d'avatar, panneau de lien…) au lieu de changer d'onglet.

   Chaque fiche ouverte ajoute une entrée d'historique à la MÊME adresse (l'ancre #onglet ne change pas, donc pas de
   « hashchange » : la navigation par onglets continue de marcher). « Retour » -> popstate -> on ferme la fiche du
   dessus. Fermée autrement (bouton ✕, Échap, enregistrement), la fiche retire son entrée (history.back(), dont le
   popstate est ignoré). Si l'athlète a changé d'onglet entre-temps, l'entrée courante n'est plus la sienne : on
   n'y touche pas. */
import { useEffect, useRef } from "react";

interface Entry { id: string; close: () => void }
const stack: Entry[] = [];
let ignore = 0;
let installed = false;
let seq = 0;

let after: (() => void)[] = [];

function onPop(): void {
  if (ignore > 0) {
    ignore -= 1;
    if (ignore === 0) { const fns = after; after = []; fns.forEach((f) => f()); }
    return;
  }
  const top = stack.pop();
  top?.close();
}

/** Exécute `fn` une fois les retours en arrière des fiches fermées terminés (ex. aller au chat après un retour de
    séance : sinon le history.back() en attente annulerait le changement d'onglet). */
export function afterBack(fn: () => void): void {
  if (ignore === 0) fn(); else after.push(fn);
}

/** Ouvre une entrée d'historique pour une fiche ; retourne la fonction à appeler quand la fiche se ferme. */
export function pushOverlay(close: () => void): () => void {
  if (!installed && typeof window !== "undefined") { window.addEventListener("popstate", onPop); installed = true; }
  const id = `ov-${Date.now().toString(36)}-${(seq += 1)}`;
  const entry: Entry = { id, close };
  stack.push(entry);
  try { history.pushState({ ...(history.state || {}), overlay: id }, "", location.href); } catch { /* bac à sable */ }
  return () => {
    const i = stack.indexOf(entry);
    if (i < 0) return;                                  // déjà fermée par « retour »
    stack.splice(i, 1);
    if ((history.state as { overlay?: string } | null)?.overlay === id) { ignore += 1; history.back(); }
  };
}

/** Pour tests : nombre de fiches ouvertes enregistrées. */
export const openOverlays = (): number => stack.length;

/** `open` passe à true : entrée d'historique ; « retour » appelle `onClose`. */
export function useBackClose(open: boolean, onClose: () => void): void {
  const ref = useRef(onClose);
  ref.current = onClose;
  useEffect(() => {
    if (!open) return undefined;
    return pushOverlay(() => ref.current());
  }, [open]);
}
