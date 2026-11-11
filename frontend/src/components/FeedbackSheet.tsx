/* Feuille de validation de séance : statut, effort ressenti, note, prévenir le coach.
   v0.38 : bilan nutrition pour les sorties ≥ 1 h 30 (le coach le reçoit et répond dans le chat). */
import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { Feedback, FeedbackResponse, NutritionSaveResponse, Session } from "../api/types";
import { dayLabel, fmtMin } from "../lib/format";
import { toast } from "../lib/toast";
import { fromLog, NutritionBlock, nutritionTouched, type NutritionValue } from "./NutritionBlock";
import { toItems } from "../lib/nutrition";
import { Sheet } from "./Sheet";

const RPE_LABELS: Record<number, string> = { 1: "très facile", 3: "facile", 5: "modéré", 6: "soutenu", 7: "dur", 8: "très dur", 10: "maximal" };

export function FeedbackSheet({ session, onClose, onSaved }: {
  session: Session | null;
  onClose: () => void;
  /** notify : le coach a été prévenu et a répondu (on passe au chat) */
  onSaved: (goToChat: boolean) => void;
}): ReactElement {
  const [status, setStatus] = useState<Feedback["status"]>("done");
  const [rpe, setRpe] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [notify, setNotify] = useState(false);
  const [saving, setSaving] = useState(false);
  const [nutri, setNutri] = useState<NutritionValue>(fromLog(null));
  useEffect(() => {
    if (!session) return;
    setStatus(session.feedback?.status || "done");
    setRpe(session.feedback?.rpe || null);
    setNote(session.feedback?.note || "");
    setNotify(false);
    setNutri(fromLog(session.nutrition));
  }, [session]);
  const minutes = session ? session.actual_min || session.duration_min : 0;
  const withNutri = !!session?.nutrition_eligible && status !== "skipped";
  const nutriOn = withNutri && nutritionTouched(nutri);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!session) return;
    if (nutriOn && nutri.digestion === null) { toast("Nutrition : indique ta digestion (1 à 5)."); document.getElementById("nutri-dig-l")?.scrollIntoView({ block: "center" }); return; }
    setSaving(true);
    try {
      const r = await api<FeedbackResponse>(`/api/sessions/${encodeURIComponent(session.session_id)}/feedback`,
        { json: { status, rpe, note, notify_coach: notify && !nutriOn } });
      if (nutriOn) {
        // le bilan part au coach (réponse en tâche de fond, visible dans le chat), avec la note si « prévenir »
        await api<NutritionSaveResponse>("/api/nutrition", { json: {
          session_id: session.session_id, items: toItems(nutri.picks), water_ml: nutri.water, digestion: nutri.digestion,
          symptoms: nutri.symptoms, comment: nutri.comment, rpe: notify ? rpe : null, note: notify ? note : "" } });
        onClose();
        toast("Séance et bilan nutrition enregistrés : le coach te répond dans le chat.", 3500);
        onSaved(true);
        return;
      }
      onClose();
      if (r.coach_error) toast("Séance validée ✓ — le coach n'a pas pu répondre : réessaie dans le chat.", 4500);
      else toast(notify ? "Séance validée, le coach a répondu" : "Séance validée ✓");
      onSaved(notify && !!r.messages?.length);
    } catch (err) { toast((err as Error).message); }
    finally { setSaving(false); }
  };

  const s = session;
  return (
    <Sheet id="fb-sheet" open={!!s} onClose={onClose}>
      <form id="fb-form" method="dialog" onSubmit={submit}>
        <h2 id="fb-title">{s?.title}</h2>
        <p id="fb-sub" className="muted">{s ? `${dayLabel(s.day)}${s.duration_min ? " · " + fmtMin(s.duration_min) : ""}` : ""}</p>
        <div className="field">
          <span className="label">Statut</span>
          <div className="seg" id="fb-status">
            {(["done", "modified", "skipped"] as const).map((v) => (
              <button key={v} type="button" data-v={v} className={status === v ? "on" : ""} onClick={() => setStatus(v)}>
                {{ done: "Faite", modified: "Modifiée", skipped: "Sautée" }[v]}
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <span className="label">Effort ressenti <span id="fb-rpe-val" className="muted">{rpe ? `${rpe}/10 ${RPE_LABELS[rpe] || ""}` : ""}</span></span>
          <div className="rpe" id="fb-rpe">
            {Array.from({ length: 10 }, (_, i) => (
              <button key={i} type="button" className={rpe === i + 1 ? "on" : ""} aria-label={`RPE ${i + 1}`}
                onClick={() => setRpe(rpe === i + 1 ? null : i + 1)}>{i + 1}</button>
            ))}
          </div>
        </div>
        <div className="field">
          <label className="label" htmlFor="fb-note">Note pour le coach</label>
          <textarea id="fb-note" rows={3} maxLength={2000} placeholder="Sensations, douleur, sommeil, contraintes…" value={note}
            onChange={(e) => { setNote(e.target.value); if (e.target.value.trim().length > 3) setNotify(true); }} />
        </div>
        {withNutri && s ? <NutritionBlock minutes={minutes} value={nutri} onChange={setNutri} /> : null}
        <label className="check"><input id="fb-notify" type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> {nutriOn ? "Joindre ma note au bilan nutrition (le coach le reçoit)" : "Prévenir le coach (il peut adapter la suite)"}</label>
        <div className="sheet-actions">
          <button type="button" className="btn" id="fb-cancel" onClick={onClose}>Annuler</button>
          <button type="submit" className="btn primary" id="fb-save" disabled={saving}>Valider</button>
        </div>
      </form>
    </Sheet>
  );
}
