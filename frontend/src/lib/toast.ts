/* Petit message en bas de l'écran : un seul à la fois, comme l'interface historique. */
type Listener = (msg: string | null) => void;
let listener: Listener | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;

export function onToast(l: Listener | null): void { listener = l; }
export function toast(msg: string, ms = 2600): void {
  listener?.(msg);
  clearTimeout(timer);
  timer = setTimeout(() => listener?.(null), ms);
}
