import { useState, type ReactElement } from "react";
import type { Activity } from "../api/types";
import { useBackClose } from "../lib/backClose";
import { comma, dayLabel, fmtMin } from "../lib/format";

const SOURCE: Record<string, string> = { hrTSS: "fréquence cardiaque (fichier)", csv_avg_hr: "fréquence cardiaque moyenne",
  estimate: "estimation (sans cardio)" };

function pace(a: Activity): string {
  if (!a.km || a.km < 0.5 || !a.minutes || !["Course", "Trail"].includes(a.sport)) return "";
  const s = (a.minutes * 60) / a.km;
  return `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}/km`;
}

/* v0.48 : une ligne d'activité se déplie (détail) ; « retour » sur Android la replie au lieu de changer d'onglet. */
export function ActRow({ a }: { a: Activity }): ReactElement {
  const [open, setOpen] = useState(false);
  useBackClose(open, () => setOpen(false));
  const bits = [fmtMin(a.minutes), a.km ? `${comma(a.km)} km` : "", a.dplus ? `${a.dplus} m D+` : "",
    a.avg_hr ? `FC ${a.avg_hr}` : ""].filter(Boolean).join(" · ");
  const p = pace(a);
  return (
    <div className={"act" + (open ? " open" : "")}>
      <button type="button" className="act-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <div><div className="t">{a.name || a.sport}{a.suspect ? <span className="badge warn" title="Durée invraisemblable"> ⚠</span> : null}</div>
          <div className="s">{`${dayLabel(a.day)} · ${bits}`}</div></div>
        <div className="l">{`charge ${a.load}`}</div>
      </button>
      {open ? (
        <div className="act-detail small">
          <div>{`${a.sport}${p ? ` · allure ${p}` : ""}`}</div>
          {a.elapsed_min ? <div>{`Temps retenu : ${fmtMin(a.minutes)} (la montre a tourné ${fmtMin(a.elapsed_min)} : pause ou montre oubliée).`}</div> : null}
          {a.suspect ? <div className="warn-text">Durée invraisemblable pour la distance : cette sortie ne compte ni dans tes records, ni dans tes trophées, ni dans ton bilan.</div> : null}
          <div>{`Charge ${a.load} · ${SOURCE[a.load_source || ""] || a.load_source || ""}`}</div>
          <button type="button" className="btn small" onClick={() => setOpen(false)}>Fermer</button>
        </div>
      ) : null}
    </div>
  );
}
