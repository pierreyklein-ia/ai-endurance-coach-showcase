/* v0.50 : « Ma semaine » (Carrière › Réglages) : le premier jour de la semaine (vue Semaine, plafonds, récap). */
import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { WeekStartView } from "../api/types";
import { toast } from "../lib/toast";

export function WeekStartCard({ active, nonce }: { active: boolean; nonce: number }): ReactElement | null {
  const [v, setV] = useState<WeekStartView | null>(null);
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<WeekStartView>("/api/week-start").then((r) => { if (!stale) setV(r); }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce]);
  if (!v) return null;
  const pick = async (day: number) => {
    try {
      const r = await api<WeekStartView>("/api/week-start", { json: { day } });
      setV(r); toast(`Ta semaine va du ${r.label} au ${r.end_label} ✓`);
    } catch (er) { toast((er as Error).message); }
  };
  return (
    <div className="card week-start" id="ma-semaine">
      <div className="eyebrow">Ma semaine</div>
      <label className="ws-row"><span>Premier jour</span>
        <select value={v.day} onChange={(e) => void pick(+e.target.value)} aria-label="Premier jour de la semaine">
          {v.days.map((d, i) => <option key={d} value={i}>{d}</option>)}
        </select></label>
      <div className="muted small">{`Semaine du ${v.label} au ${v.end_label} : vue Semaine, volume maximum, récap du ${v.end_label} soir et semaines du plan.`}</div>
    </div>
  );
}
