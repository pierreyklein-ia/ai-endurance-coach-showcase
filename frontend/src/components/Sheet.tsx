/* Feuille modale (<dialog> natif) : ouverte/fermée par la propriété `open`. */
import { useEffect, useRef, type ReactElement, type ReactNode } from "react";
import { useBackClose } from "../lib/backClose";

export function Sheet({ id, className = "sheet", open, onClose, children }: {
  id: string; className?: string; open: boolean; onClose: () => void; children: ReactNode;
}): ReactElement {
  const ref = useRef<HTMLDialogElement>(null);
  useBackClose(open, onClose);                 // v0.48 : « retour » (Android) ferme la feuille
  useEffect(() => {
    const d = ref.current; if (!d) return;
    if (open && !d.open) d.showModal?.();
    else if (!open && d.open) d.close();
  }, [open]);
  return <dialog id={id} ref={ref} className={className} onClose={onClose}>{children}</dialog>;
}
