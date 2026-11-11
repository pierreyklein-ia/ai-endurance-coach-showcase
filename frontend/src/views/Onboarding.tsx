/* premier lancement (v0.11) : connexion Strava, lecture de l'historique, ou import de fichiers FIT */
import { useEffect, useRef, useState, type ChangeEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { CoachPersona, Onboarding as OnboardingT, StravaConnect } from "../api/types";
import { CoachAvatar } from "../components/CoachAvatar";
import { CoachPicker } from "../components/CoachPicker";
import { DEFAULT_COACH, yourCoach } from "../lib/coach";
import { toast } from "../lib/toast";

export function Onboarding({ active, nonce, onDone, onCoach }: {
  active: boolean; nonce: number; onDone: () => void;
  /** v0.36 : coach choisi au premier lancement */
  onCoach?: (c: CoachPersona) => void;
}): ReactElement {
  const [ob, setOb] = useState<OnboardingT | null>(null);
  const [coach, setCoach] = useState<CoachPersona | null>(null);
  const [tick, setTick] = useState(0);
  const [connecting, setConnecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const activeRef = useRef(active);
  activeRef.current = active;

  useEffect(() => {
    if (!active) return;
    let stale = false, timer: ReturnType<typeof setTimeout> | undefined;
    api<OnboardingT>("/api/onboarding").then((r) => {
      if (stale || !activeRef.current) return;
      if (r.step === "done" || r.step === "chat") { onDone(); return; }
      setOb(r);
      if (r.step === "syncing") timer = setTimeout(() => setTick((n) => n + 1), 2000);
    }).catch(() => {});
    return () => { stale = true; clearTimeout(timer); };
  }, [active, nonce, tick]);
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<CoachPersona>("/api/coach").then((c) => { if (!stale) setCoach(c); })
      .catch(() => { if (!stale) setCoach({ ...DEFAULT_COACH, chosen: true }); });   // pas de blocage : coach par défaut
    return () => { stale = true; };
  }, [active, nonce]);
  const refresh = () => setTick((n) => n + 1);

  const onFiles = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = [...(e.target.files || [])]; e.target.value = "";
    let ok = 0;
    for (const [i, file] of files.entries()) {
      toast(`Import ${i + 1}/${files.length}…`, 10000);
      try { await api(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file }); ok++; } catch { /* suivant */ }
    }
    toast(`${ok}/${files.length} fichier(s) importé(s)`); refresh();
  };
  const connect = async () => {
    setConnecting(true);
    try {
      const r = await api<StravaConnect>("/api/strava/connect", { json: { origin: location.origin } });
      if (r.status === "redirect" && r.url) { location.href = r.url; return; }
      if (r.status === "connected") { setConnecting(false); refresh(); return; }
      toast(r.message || "Connexion impossible", 6000);
    } catch (err) { toast((err as Error).message, 6000); }
    setConnecting(false);
  };
  const start = async () => {
    setStarting(true);
    try { await api("/api/onboarding/start", { method: "POST" }); onDone(); }
    catch (err) { toast((err as Error).message, 5000); setStarting(false); }
  };

  let body: ReactElement | null = null;
  if (ob && ob.step === "syncing") {
    body = (
      <section className="welcome">
        <img src="/static/icon.svg" alt="" className="login-logo" />
        <h1>Je lis ton historique</h1>
        <p className="progress-line">{ob.job?.message || "Connexion à Strava…"}</p>
        <div className="spinner" aria-hidden="true"></div>
        <p className="muted small">Je remonte jusqu'à 5 ans en arrière : une à deux minutes la première fois.</p>
      </section>
    );
  } else if (ob && coach && !coach.chosen) {
    // v0.36 : avant Strava, l'athlète choisit son coach (modifiable ensuite dans Carrière › Réglages)
    body = (
      <section className="welcome coach-setup">
        <h1>Choisis ton coach</h1>
        <p>Qui veux-tu à tes côtés ? Tu pourras changer d'avis plus tard dans Carrière › Réglages (touche ton avatar en haut).</p>
        <div className="card coach-setup-card">
          <CoachPicker value={coach} submitLabel="C'est parti" onSaved={(c) => { setCoach(c); onCoach?.(c); }} />
        </div>
      </section>
    );
  } else if (ob && coach) {
    // étape « connect » : écran d'accueil
    const err = ob.job?.error || (ob.connect === "error" ? ob.connect_error : null);
    body = (
      <section className="welcome">
        {coach ? <CoachAvatar gender={coach.gender} personality={coach.personality} size={110} /> : <img src="/static/icon.svg" alt="" className="login-logo" />}
        <h1>{coach ? `${coach.name}, ${yourCoach(coach.gender)} d'endurance` : "Ton coach d'endurance"}</h1>
        <p>Je commence par lire tes entraînements pour comprendre où tu en es. Ensuite on discute de ton objectif et je te propose un plan, que tu peux ajuster avec moi.</p>
        {ob.strava ? <button className="btn primary strava" disabled={connecting} onClick={connect}>
          {connecting ? "Ouverture de Strava…" : ob.strava_connected ? "Continuer avec Strava" : "Connecter Strava"}</button>
          : <p className="error">Strava n'est pas configuré sur ce serveur (Client ID / Secret : relance « bash install.sh »).</p>}
        <p className="muted small">Je lis tes activités (lecture seule) pour ton usage personnel. Rien n'est publié sur Strava.</p>
        {err ? <p className="error">{err}</p> : null}
        <details className="alt"><summary>Pas de Strava ? Importer des fichiers FIT</summary>
          <p className="muted">Importe tes sorties des derniers mois (fichiers .fit exportés de ta montre).</p>
          <div className="row-actions">
            <label className="btn"><input type="file" accept=".fit,.gpx,.gz" multiple hidden onChange={onFiles} />Choisir des fichiers</label>
            {ob.activities ? <button className="btn primary" disabled={starting} onClick={start}>{starting ? "Le coach analyse…" : `Faire mon bilan (${ob.activities} sorties)`}</button> : null}
          </div>
        </details>
      </section>
    );
  }
  return <section id="view-onboarding" className="view" data-view="onboarding" hidden={!active}>{body}</section>;
}
