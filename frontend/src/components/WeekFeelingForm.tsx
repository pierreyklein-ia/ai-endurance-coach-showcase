/* v0.51 : ressenti de fin de semaine (fatigue, difficulté, plaisir), envoyé au coach depuis le récap. */
import { useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { WeekFeeling } from "../api/types";
import { toast } from "../lib/toast";

const ROWS: { key: "fatigue" | "difficulty" | "fun"; label: string; lo: string; hi: string }[] = [
  { key: "fatigue", label: "Fatigue", lo: "en forme", hi: "épuisé" },
  { key: "difficulty", label: "Difficulté", lo: "facile", hi: "très dure" },
  { key: "fun", label: "Plaisir", lo: "corvée", hi: "top" },
];

export function WeekFeelingForm({ weekStart, initial, onSaved }: {
  weekStart: string; initial?: WeekFeeling | null; onSaved?: (f: WeekFeeling) => void;
}): ReactElement {
  const [v, setV] = useState<Record<string, number>>({ fatigue: initial?.fatigue ?? 0, difficulty: initial?.difficulty ?? 0, fun: initial?.fun ?? 0 });
  const [note, setNote] = useState(initial?.note ?? "");
  const [busy, setBusy] = useState(false);
  const ready = ROWS.every((r) => v[r.key] >= 1);
  const send = async () => {
    setBusy(true);
    try {
      const f = await api<WeekFeeling>("/api/recap/feeling", { json: { week_start: weekStart, ...v, note } });
      toast("Ressenti envoyé : le coach te répond dans le chat.", 4000);
      onSaved?.(f);
    } catch (e) { toast((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <section className="recap-sec week-feeling" aria-label="Ton ressenti de la semaine">
      <h3>{initial ? "Ton ressenti (modifiable)" : "Ton ressenti de la semaine"}</h3>
      {ROWS.map((r) => (
        <div key={r.key} className="feel-row" role="radiogroup" aria-label={r.label}>
          <span className="feel-label">{r.label}</span>
          <span className="feel-scale">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} type="button" role="radio" aria-checked={v[r.key] === n} aria-label={`${r.label} ${n} sur 5`}
                className={"feel-dot" + (v[r.key] === n ? " on" : "")} onClick={() => setV({ ...v, [r.key]: n })}>{n}</button>
            ))}
          </span>
          <span className="feel-hint muted small">{`1 ${r.lo} · 5 ${r.hi}`}</span>
        </div>
      ))}
      <textarea className="feel-note" rows={2} maxLength={500} placeholder="Un mot pour le coach (facultatif)"
        value={note} onChange={(e) => setNote(e.target.value)} />
      <button type="button" className="btn small primary" disabled={!ready || busy} onClick={() => void send()}>
        {initial ? "Mettre à jour" : "Envoyer au coach"}</button>
    </section>
  );
}
