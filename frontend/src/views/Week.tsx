import { useEffect, useRef, useState, type ReactElement } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../api/client";
import type { MoveResponse, RecapView, Session, WeekView } from "../api/types";
import { SessionCard } from "../components/SessionCard";
import { Sheet } from "../components/Sheet";
import { canDrag, dropRefusal, dropWarning, moveLocal, raceDays } from "../lib/drag";
import { addDays, comma, dayLabel, fmtMin, STATUS } from "../lib/format";
import { toast } from "../lib/toast";
import { afterBack, useBackClose } from "../lib/backClose";
import { isAlert, recapToShow, withFeeling } from "../lib/recap";
import { WeekFeelingForm } from "../components/WeekFeelingForm";
import { useSessionDrag } from "../lib/useSessionDrag";

const itemMeta = (s: Session): string => [fmtMin(s.duration_min), s.key ? "clé" : ""].filter(Boolean).join(" · ");

export function Week({ active, nonce, onFeedback, onMoved }: {
  active: boolean; nonce: number; onFeedback: (s: Session) => void; onMoved?: () => void;
}): ReactElement {
  const [w, setW] = useState<WeekView | null>(null);
  const start = useRef<string | null>(null);   // semaine affichée (gardée d'une visite à l'autre)
  const [detail, setDetail] = useState<Session | null>(null);
  useBackClose(!!detail, () => setDetail(null));       // v0.48 : « retour » ferme le détail, pas l'onglet
  const [reload, setReload] = useState(0);
  const [picker, setPicker] = useState<Session | null>(null);   // « Déplacer » au clavier / lecteur d'écran
  const [busy, setBusy] = useState<string | null>(null);        // séance en cours d'envoi
  const [snap, setSnap] = useState<string | null>(null);        // séance revenue à sa place (refus)
  const [recap, setRecap] = useState<RecapView | null>(null);   // v0.50 : « Récap de la semaine »
  const [recapOpen, setRecapOpen] = useState(false);
  const openRecap = async () => {
    try { setRecap(await api<RecapView>("/api/recap")); setRecapOpen(true); }
    catch (e) { toast((e as Error).message); }
  };
  const section = useRef<HTMLElement>(null);
  const wRef = useRef<WeekView | null>(null);
  wRef.current = w;

  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<WeekView>("/api/week" + (start.current ? `?start=${start.current}` : "")).then((r) => {
      if (stale) return;
      start.current = r.start;
      setW(r); setDetail(null);
    }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce, reload]);

  const goWeek = (iso: string) => { start.current = iso; setReload((n) => n + 1); };

  const move = async (id: string, day: string) => {
    const before = wRef.current;
    if (!before) return;
    const s = before.days.flatMap((d) => d.sessions).find((x) => x.session_id === id);
    if (!s) return;
    const why = dropRefusal(s, day, before.today, raceDays(before));
    if (why) { if (why !== "même jour") { toast(`Impossible : ${why}.`); setSnap(id); } return; }
    setW(moveLocal(before, id, day));                     // optimiste : la séance est déjà au nouveau jour
    setBusy(id);
    try {
      const r = await api<MoveResponse>(`/api/sessions/${encodeURIComponent(id)}/move`, { json: { day } });
      // v0.38 : jamais de refus pour raison d'entraînement ; les avertissements sont résumés ici, le coach commente
      const alerts = r.alerts ?? r.warnings.map((text) => ({ text, level: "warn" as const, would_block: false }));
      const strong = alerts.filter((a) => a.level === "strong");
      const tail = r.pending ? "Le coach attend toujours ta réponse dans le chat." : "Le coach te répond dans le chat.";
      toast(alerts.length ? `Séance déplacée. ${strong.length ? "⚠ " : ""}À surveiller : ${alerts.map((a) => a.text).join(" ; ")}. ${tail}`
        : `Séance déplacée. ${tail}`, alerts.length ? 7000 : 3200);
    } catch (e) {
      setW(before);                                        // refus (ou erreur) : retour à sa place, le serveur fait foi
      setSnap(id);
      if (e instanceof ApiError) toast(e.message, 6500);
      else toast("Connexion interrompue : je recharge la semaine et le chat pour voir où en est la séance.", 6500);
    } finally {
      setBusy(null);
      setReload((n) => n + 1);                             // le serveur fait foi (repos échangé, autres changements)
      onMoved?.();                                         // v0.38 : le chat est relu dans tous les cas
    }
  };

  const { ghost, over, onPointerDown, consumeClick } = useSessionDrag(section, (id, day) => void move(id, day));
  useEffect(() => { if (!snap) return; const t = setTimeout(() => setSnap(null), 600); return () => clearTimeout(t); }, [snap]);

  let body: ReactElement | null = null;
  if (detail) {
    body = <><div className="week-nav"><button className="btn small" onClick={() => setReload((n) => n + 1)}>‹ Semaine</button></div><SessionCard s={detail} onFeedback={onFeedback} today={w?.today}
      onChanged={() => { onMoved?.(); setReload((n) => n + 1); }} /></>;
  } else if (w) {
    const pct = w.planned_min ? Math.min(100, Math.round((100 * w.done_min) / w.planned_min)) : 0;
    const anySessions = w.days.some((d) => d.sessions.length);
    const races = raceDays(w);
    const dragging = ghost ? w.days.flatMap((d) => d.sessions).find((x) => x.session_id === ghost.id) || null : null;
    const anyDraggable = w.days.some((d) => d.sessions.some((s) => canDrag(s, w.today)));
    body = <>
      <div className="week-nav">
        <button className="btn small" onClick={() => goWeek(addDays(w.start, -7))} aria-label="Semaine précédente">‹</button>
        <div className="title">{`Semaine du ${dayLabel(w.start)}`}{w.phase ? <div className="muted">{w.phase}</div> : null}</div>
        <button className="btn small" onClick={() => goWeek(addDays(w.start, 7))} aria-label="Semaine suivante">›</button>
      </div>
      <div className="card">
        <div className="card-head"><span className="eyebrow">Fait / prévu</span>
          <span className="muted">{`${fmtMin(w.done_min) || "0 min"} / ${fmtMin(w.planned_min) || "—"}`}</span></div>
        <div className="progress"><i style={{ width: pct + "%" }}></i></div>
        {w.focus ? <div>{w.focus}</div> : null}
        {w.review ? <details><summary>{`Bilan du coach${w.reviewed_at ? " · " + w.reviewed_at : ""}`}</summary><div className="note">{w.review}</div></details> : null}
        <div className="row-actions"><button type="button" className="btn small recap-btn" onClick={() => void openRecap()}>
          {"📋 Récap de la semaine"}{w.recap?.last ? <span className="small muted">{` · dernier : ${dayLabel(w.recap.last)}`}</span> : null}</button></div>
      </div>
      {anyDraggable ? <div className="muted small drag-hint">Appui long sur une séance (ou glisser à la souris) pour la changer de jour.</div> : null}
      {!anySessions && w.keys.length ? (
        <div className="card">
          <div className="eyebrow">Séances clés prévues</div>
          <div className="muted">Le détail jour par jour arrive avec le bilan du dimanche précédent.</div>
          {w.keys.map((k, i) => <div key={i} className="act"><div className="t">{k.day}</div><div>{k.text}</div></div>)}
        </div>
      ) : null}
      {w.days.map((d) => {
        let drop = "";
        let warn: ReturnType<typeof dropWarning> = null;
        if (dragging) {
          const why = dropRefusal(dragging, d.date, w.today, races);
          drop = why === "même jour" ? " drop-home" : why ? " drop-no" : " drop-ok";
          warn = why ? null : dropWarning(dragging, d, races);
          if (warn) drop += warn.kind === "two_hard" ? " drop-hard" : " drop-race";
          if (over === d.date) drop += " drop-over";
        }
        // v0.38 : jour à deux séances dures (ou dépôt qui en créerait un) : contour rouge + libellé
        const hard = d.alert?.kind === "two_hard" || (warn?.kind === "two_hard" && over === d.date);
        const label = d.alert?.label || (hard ? warn?.label : null);
        return (
          <div key={d.date} data-drop-day={d.date} className={`day ${d.date === w.today ? "today" : ""}${d.date < w.today ? " past" : ""}${hard ? " alert-two-hard" : ""}${drop}`}>
            <div className="day-head"><span>{dayLabel(d.date)}</span>
              <span className="day-badges">
                {hard && label ? <span className="day-alert" role="note"><b aria-hidden="true">!</b>{label}</span> : null}
                {warn?.kind === "race" && over === d.date ? <span className="day-alert race" role="note"><b aria-hidden="true">!</b>jour de course</span> : null}
                {d.date === w.today ? <span className="badge key">aujourd'hui</span> : null}
              </span></div>
            <div className="day-body">
              {d.sessions.length ? d.sessions.map((s) => {
                const can = canDrag(s, w.today);
                const cls = "item" + (can ? " draggable" : "") + (ghost?.id === s.session_id ? " drag-src" : "")
                  + (busy === s.session_id ? " busy" : "") + (snap === s.session_id ? " snap-back" : "");
                return (
                  <div key={s.session_id} className={cls} data-session={s.session_id}
                    onPointerDown={can ? (e) => onPointerDown(e, s.session_id, s.title, itemMeta(s)) : undefined}
                    onClick={() => { if (consumeClick()) return; setDetail(s); window.scrollTo(0, 0); }}>
                    <div><div className="it">{s.title}</div><div className="im">{itemMeta(s)}</div></div>
                    <span className="item-end">
                      {s.feedback ? <span className={`badge ${s.feedback.status}`} title={s.feedback.auto ? "validée automatiquement par ta sortie" : undefined}>{STATUS[s.feedback.status] + (s.feedback.auto ? " ⚡" : "")}</span> : null}
                      {can ? <button type="button" className="move-btn" aria-label={`Déplacer « ${s.title} »`} title="Déplacer"
                        onClick={(e) => { e.stopPropagation(); setPicker(s); }}>
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 7l4-4 4 4M12 3v18M8 17l4 4 4-4" /></svg>
                      </button> : s.feedback ? null : <span className="muted">›</span>}
                    </span>
                  </div>
                );
              }) : <div className="off">{anySessions ? "Repos" : "À détailler"}</div>}
              {d.activities.map((a, i) => <div key={i} className="off">{`✓ ${a.name || a.sport} · ${fmtMin(a.minutes)}${a.km ? " · " + comma(a.km) + " km" : ""}`}</div>)}
            </div>
          </div>
        );
      })}
    </>;
  }
  const pw = w;
  return <section id="view-semaine" ref={section} className="view" data-view="semaine" hidden={!active}>
    {body}
    {ghost ? createPortal(
      <div className="drag-ghost" aria-hidden="true"
        style={{ width: ghost.w, transform: `translate(${ghost.x - ghost.offX}px, ${ghost.y - ghost.offY}px)` }}>
        <div className="it">{ghost.title}</div><div className="im">{ghost.meta}</div>
      </div>, document.body) : null}
    <Sheet id="recap-sheet" open={recapOpen} onClose={() => setRecapOpen(false)}>
      {recapOpen ? (() => {
        const r = recapToShow(recap, w?.start || "");
        return <form method="dialog" onSubmit={(e) => e.preventDefault()} className="recap">
          <h2>Récap de la semaine</h2>
          {r ? <>
            <p className="muted small">{`Semaine du ${dayLabel(r.item.week_start)} au ${dayLabel(r.item.week_end)}` + (r.preview ? " · aperçu (le récap est posté le soir du dernier jour)" : r.item.at ? ` · posté le ${dayLabel(r.item.at.slice(0, 10))} à ${r.item.at.slice(11, 16)}` : "")}</p>
            {r.item.sections.map((sec) => <section key={sec.id} className={`recap-sec recap-${sec.id}`}><h3>{sec.title}</h3>
              <ul>{sec.lines.map((ln, i) => <li key={i} className={isAlert(ln) ? "recap-alert" : undefined}>{ln}</li>)}</ul></section>)}
            <WeekFeelingForm key={r.item.week_start} weekStart={r.item.week_start} initial={r.item.feeling}
              onSaved={(f) => setRecap((cur) => withFeeling(cur, r.item.week_start, f))} />
          </> : <p className="muted">Pas encore de récap : il arrive le soir du dernier jour de ta semaine.</p>}
          <div className="sheet-actions">
            {/* la feuille fermée retire son entrée d'historique (history.back) : on change d'onglet APRÈS, sinon le
                retour en arrière annulerait le passage au chat */}
            <button type="button" className="btn" onClick={() => { setRecapOpen(false); setTimeout(() => afterBack(() => { location.hash = "#coach"; }), 0); }}>Répondre au coach</button>
            <button type="button" className="btn primary" onClick={() => setRecapOpen(false)}>Fermer</button></div>
        </form>;
      })() : null}
    </Sheet>
    <Sheet id="move-sheet" open={!!picker} onClose={() => setPicker(null)}>
      {picker && pw ? (
        <form method="dialog" onSubmit={(e) => e.preventDefault()}>
          <h2>{`Déplacer « ${picker.title} »`}</h2>
          <p className="muted small">{`Prévue ${dayLabel(picker.day)}. Choisis le nouveau jour : c'est toi qui décides, le coach vérifie la charge et te répond dans le chat.`}</p>
          <div className="day-pick" role="group" aria-label="Nouveau jour">
            {pw.days.map((d) => {
              const why = dropRefusal(picker, d.date, pw.today, raceDays(pw));
              const wn = why ? null : dropWarning(picker, d, raceDays(pw));
              return <button key={d.date} type="button" className={"btn" + (why === "même jour" ? " current" : "") + (wn ? " warn" : "")} disabled={!!why}
                title={why || wn?.label || undefined} onClick={() => { const s = picker; setPicker(null); void move(s.session_id, d.date); }}>
                {dayLabel(d.date)}{why && why !== "même jour" ? <span className="small muted">{` · ${why}`}</span> : null}
                {why === "même jour" ? <span className="small muted"> · actuel</span> : null}
                {wn ? <span className="small pick-warn">{` · ⚠ ${wn.label}`}</span> : null}
              </button>;
            })}
          </div>
          <div className="sheet-actions"><button type="button" className="btn" onClick={() => setPicker(null)}>Annuler</button></div>
        </form>
      ) : null}
    </Sheet>
  </section>;
}
