import type { ReactElement } from "react";
import type { Session, Term } from "../api/types";
import { dayLabel, fmtMin, STATUS } from "../lib/format";
import { targetLine } from "../lib/zones";
import { LinkPanel } from "./LinkPanel";

const isoToday = (): string => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };

/** v0.48 : vocabulaire simple : « C'est quoi ? » explique les termes techniques de la séance */
export function Glossary({ terms }: { terms: Term[] }): ReactElement {
  return (
    <details className="glossary">
      <summary>C'est quoi ?</summary>
      <dl>{terms.map((t) => <div key={t.term}><dt>{t.term}</dt><dd>{t.text}</dd></div>)}</dl>
    </details>
  );
}

export function SessionCard({ s, full = true, onFeedback, today, onChanged }: {
  s: Session; full?: boolean; onFeedback: (s: Session) => void;
  /** v0.44 : jour du serveur, et rechargement après un lien / déliement avec une sortie */
  today?: string; onChanged?: () => void;
}): ReactElement {
  const fb = s.feedback;
  const meta = [fmtMin(s.duration_min), s.elevation_gain_m ? `${s.elevation_gain_m} m D+` : ""].filter(Boolean).join(" · ");
  return (
    <article className="card">
      <div className="card-head">
        <span className="eyebrow">{dayLabel(s.day)}</span>
        <span>
          {s.key ? <span className="badge key">clé</span> : null}{" "}
          {fb ? <span className={`badge ${fb.status}`}>{STATUS[fb.status] + (fb.rpe ? ` · ${fb.rpe}/10` : "")}</span> : null}
        </span>
      </div>
      <div className="session-title">{s.title}</div>
      {s.orig_title ? <div className="muted small orig-title">{`Changée par le coach · prévue au départ : ${s.orig_title}`}</div> : null}
      {meta ? <div className="session-meta">{meta}</div> : null}
      {s.target ? <div className="session-target" title={s.target.text}>
        <span className="tgt-zone">{s.target.zone}</span><span>{targetLine(s.target)}</span>
        {full && s.target.note ? <div className="muted small tgt-note">{s.target.note}</div> : null}
      </div> : null}
      {full && s.detail ? <div className="session-detail">{s.detail}</div> : null}
      {full && s.terms?.length ? <Glossary terms={s.terms} /> : null}
      {fb && fb.note ? <div className="note">{fb.note}</div> : null}
      {fb?.auto ? <div className="auto-valid small">{`✓ ${fb.auto.manual ? "Liée" : "Validée automatiquement"} avec ta sortie « ${fb.auto.activity} » (${fb.auto.minutes} min`
        + (fb.auto.km ? `, ${String(fb.auto.km).replace(".", ",")} km` : "") + ")" + (fb.rpe ? "." : " : ajoute ton ressenti pour que le coach s'ajuste.")}
        {fb.auto.type && fb.auto.type.verdict !== "non_verifie" ? <div className={"type-check " + fb.auto.type.verdict}>
          {fb.auto.type.ok ? `Type détecté : ${fb.auto.type.label} ✓` + (fb.auto.type.verdict === "origine" ? " (séance prévue au départ)" : "") : `Type détecté : ${fb.auto.type.label}, ${fb.auto.type.text}`}</div> : null}</div> : null}
      {full ? <LinkPanel s={s} today={today || isoToday()} onChanged={onChanged} /> : null}
      {full ? (
        <div className="row-actions">
          <button className={`btn ${fb && !(fb.auto && !fb.rpe) ? "" : "primary"}`} onClick={() => onFeedback(s)}>{fb?.auto && !fb.rpe ? "Ajouter mon ressenti" : fb ? "Modifier mon retour" : "Valider la séance"}</button>
        </div>
      ) : null}
    </article>
  );
}
