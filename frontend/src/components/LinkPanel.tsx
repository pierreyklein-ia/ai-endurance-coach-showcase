/* v0.44 : lier une séance prévue à la sortie réellement faite (± 3 jours), ou la délier. */
import { useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { LinkCandidates, Session } from "../api/types";
import { useBackClose } from "../lib/backClose";
import { dayLabel } from "../lib/format";
import { toast } from "../lib/toast";

const kmFr = (k: number): string => String(k).replace(".", ",");
const VERDICT_CLS: Record<string, string> = { conforme: "good", origine: "good", non_verifie: "muted", plus_facile: "warn", plus_dur: "warn", autre: "warn" };

export function LinkPanel({ s, today, onChanged }: { s: Session; today: string; onChanged?: () => void }): ReactElement | null {
  const [open, setOpen] = useState(false);
  useBackClose(open, () => setOpen(false));            // v0.48 : « retour » referme le panneau
  const [busy, setBusy] = useState(false);
  const [c, setC] = useState<LinkCandidates | null>(null);
  const linked = s.feedback?.auto;
  const horizon = new Date(new Date(today + "T12:00:00").getTime() + 3 * 864e5).toISOString().slice(0, 10);
  // une séance à venir (≤ 3 jours) peut aussi recevoir une sortie déjà faite : elle est alors avancée à ce jour-là
  if (s.day > horizon || s.is_race || (s.intensity === "rest" && s.kind !== "renfo" && s.kind !== "mobilite")) return null;

  const load = async () => {
    setOpen(true);
    try { setC(await api<LinkCandidates>(`/api/sessions/${encodeURIComponent(s.session_id)}/link`)); }
    catch (e) { toast((e as Error).message); setOpen(false); }
  };
  const link = async (start: string) => {
    setBusy(true);
    try {
      const r = await api<{ moved_from: string | null; day: string }>(`/api/sessions/${encodeURIComponent(s.session_id)}/link`,
        { json: { activity_start: start } });
      toast(r.moved_from ? `Séance liée et déplacée au ${dayLabel(r.day)} : le coach te répond dans le chat.`
        : "Séance liée à ta sortie : le coach te répond dans le chat.", 4500);
      setOpen(false); onChanged?.();
    } catch (e) { toast((e as Error).message); }
    finally { setBusy(false); }
  };
  const unlink = async () => {
    setBusy(true);
    try {
      await api(`/api/sessions/${encodeURIComponent(s.session_id)}/link`, { method: "DELETE" });
      toast("Séance déliée : elle est de nouveau à valider.");
      onChanged?.();
    } catch (e) { toast((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="link-panel">
      <div className="row-actions">
        <button className="btn small" disabled={busy} onClick={() => (open ? setOpen(false) : void load())}>
          {linked ? "Changer la sortie liée" : "Lier une sortie"}</button>
        {linked ? <button className="btn small" disabled={busy} onClick={() => void unlink()}>Délier</button> : null}
      </div>
      {open ? (
        <div className="link-list" role="list" aria-label="Sorties possibles">
          {!c ? <div className="muted small">Recherche de tes sorties…</div>
            : !c.candidates.length ? <div className="muted small">Aucune sortie du même sport à 3 jours ou moins de cette séance.</div>
            : c.candidates.map((a) => (
              <button key={a.start} role="listitem" className={"link-item" + (a.current ? " current" : "")} disabled={busy || a.current}
                onClick={() => void link(a.start)}>
                <span className="li-main"><b>{a.activity}</b>{` · ${dayLabel(a.day)} · ${a.minutes} min` + (a.km ? ` · ${kmFr(a.km)} km` : "")}</span>
                <span className="li-sub">
                  {a.type ? <span className={"chip " + (VERDICT_CLS[a.type.verdict] || "")}>{a.type.verdict === "non_verifie" ? "type non vérifié"
                    : a.type.ok ? `${a.type.label} ✓` : `${a.type.label} : ${a.type.text}`}</span> : null}
                  {a.current ? <span className="chip on">liée</span> : a.linked_title ? <span className="chip">{`liée à « ${a.linked_title} »`}</span> : null}
                  {a.day !== s.day && !a.current ? <span className="chip">{`la séance passera au ${dayLabel(a.day)}`}</span> : null}
                </span>
              </button>
            ))}
        </div>
      ) : null}
    </div>
  );
}
