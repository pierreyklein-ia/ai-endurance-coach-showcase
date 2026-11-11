/* v0.35 : décor du thème cel-shading qui suit l'onglet (montagne à gauche → mer à droite) et sens de l'animation. */
const ORDER = ["aujourdhui", "semaine", "coach", "progres", "etat", "course"];

/** Position du panorama pour une vue (0 = tout à gauche, 1 = tout à droite) ; les vues hors onglets restent à gauche. */
export function sceneryPos(view: string): number {
  const i = ORDER.indexOf(view);
  return i < 0 ? 0 : i / (ORDER.length - 1);
}

let last = 0;
/** Publie la position (variable CSS --scenery) et le sens du déplacement (data-nav-dir) sur <html>. */
export function applyScenery(view: string, root: HTMLElement = document.documentElement): void {
  const p = sceneryPos(view);
  root.style.setProperty("--scenery", String(p));
  root.dataset.navDir = p > last ? "right" : p < last ? "left" : "none";
  last = p;
}
