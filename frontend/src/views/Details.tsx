/* réglages (v0.28, dans la page Carrière depuis v0.39) : coach, thème, version, connexions, notifications, déconnexion, remise à zéro */
import { useEffect, useState, type ReactElement, type ReactNode } from "react";
import { api } from "../api/client";
import type { About, AgentsInfo, ClaudeCheck, CoachPersona, Me, StravaConnect } from "../api/types";
import { CoachPicker } from "../components/CoachPicker";
import { WeekMaxCard } from "../components/WeekMaxCard";
import { WeekStartCard } from "../components/WeekStartCard";
import { ZonesCard } from "../components/ZonesCard";
import { eur } from "../lib/format";
import { enablePush } from "../lib/push";
import { applyTheme, currentTheme, THEMES } from "../lib/theme";
import { toast } from "../lib/toast";
import { APP_VERSION } from "../version";

/** v0.40 : mode d'agents et équipe (discret : replié par défaut). */
function AgentsPanel({ info }: { info: AgentsInfo }): ReactElement {
  return (
    <details className="alt agents-panel">
      <summary><Row k="Agents" v={info.mode === "multi" ? `équipe (${info.team.length} agents)` : "mono-agent (comparaison)"} /></summary>
      <div className="muted small">{info.note}</div>
      <ul className="agents-list">
        {info.all.map((r) => (
          <li key={r.key} className={info.team.some((t) => t.key === r.key) ? undefined : "off"}>
            <span className="agent-av sm" aria-hidden="true">{r.icon}</span>
            <span className="agents-who"><b>{r.label}</b> <span className="tier-chip">{r.tier === "main" ? "grand modèle" : "petit modèle"}</span>
              <span className="muted small agents-role">{r.role}</span>
              {r.tools.length ? <span className="card-chips">{r.tools.map((t) => <span key={t} className="tool-chip">{t}</span>)}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <div className="muted small">{`Œil du coach : niveau ${info.eye_level}, une analyse tous les ${info.eye_every_days} jours.`}</div>
    </details>
  );
}

const Row = ({ k, v }: { k: string; v: ReactNode }) => <div className="kv"><span className="k">{k}</span><span className="v">{v}</span></div>;

export function Details({ active, nonce, me, onLogout, onReset, onCoach, embedded }: {
  active: boolean; nonce: number; me: Me | null; onLogout: () => void; onReset: () => void;
  onCoach?: (c: CoachPersona) => void;
  /** v0.39 : affiché dans la page Carrière (bloc « Réglages ») plutôt que comme une vue à part */
  embedded?: boolean;
}): ReactElement {
  const [a, setA] = useState<About | null>(null);
  const [theme, setTheme] = useState(currentTheme());
  const [gem, setGem] = useState("");
  const [gemBusy, setGemBusy] = useState(false);
  const [cl, setCl] = useState<ClaudeCheck | null>(null);
  const [clBusy, setClBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!active) return;
    let stale = false;
    setArmed(false);
    api<About>("/api/about").then((r) => { if (!stale) setA(r); }).catch(() => { if (!stale) setA({}); });   // page partielle
    return () => { stale = true; };
  }, [active, nonce]);

  const m = me || ({} as Partial<Me>);
  const dict = a?.dictation || {};
  const img = a?.image || {};
  const testGemini = async () => {
    setGemBusy(true); toast("Test de la clé Gemini…", 8000);
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/about/image-check", { method: "POST" });
      toast((r.ok ? "✓ " : "✗ ") + r.message, 9000);
      setGem((r.ok ? "✓ " : "✗ ") + r.message);
    } catch (err) { toast((err as Error).message); }
    setGemBusy(false);
  };
  const testClaude = async () => {                       // v0.48 : erreur exacte de Claude (clé, modèle, requête)
    setClBusy(true); setCl(null); toast("Test de Claude…", 8000);
    try {
      const r = await api<ClaudeCheck>("/api/about/claude-check", { method: "POST" });
      setCl(r); toast(r.ok ? "✓ Claude répond" : "✗ Claude ne répond pas correctement", 6000);
    } catch (err) { toast((err as Error).message); }
    setClBusy(false);
  };
  const connectStrava = async () => {
    try {
      const r = await api<StravaConnect>("/api/strava/connect", { json: { origin: location.origin, force: true } });
      if (r.status === "redirect" && r.url) location.href = r.url; else toast(r.message || "Strava connecté ✓");
    } catch (e) { toast((e as Error).message); }
  };
  const reset = async () => {
    if (!armed) { setArmed(true); return; }
    await api("/api/reset", { json: { confirm: "recommencer" } });
    onReset();
  };

  const content = a ? <>
        {!m.onboarding ? <WeekMaxCard active={active} nonce={nonce} onChat={() => { location.hash = "#coach"; }} /> : null}
        {!m.onboarding ? <WeekStartCard active={active} nonce={nonce} /> : null}
        {!m.onboarding ? <ZonesCard active={active} nonce={nonce} /> : null}
        <div className="card coach-card" id="coach-card">
          <div className="eyebrow">Mon coach</div>
          <div className="muted small">Son ton dans le chat. Les garde-fous et le plan ne changent pas.</div>
          <CoachPicker value={m.coach} submitLabel="Enregistrer" onSaved={(c) => { onCoach?.(c); toast(`${c.name} est ${c.gender === "femme" ? "prête" : "prêt"} ✓`); }} />
        </div>
        <div className="card">
          <div className="eyebrow">Thème (à l'essai)</div>
          <div className="muted small">Change l'apparence de toute l'app, sur cet appareil seulement.</div>
          <div className="seg theme-pick">
            {THEMES.map(([k, label]) => (
              <button key={k} type="button" className={theme === k ? "on" : undefined} onClick={() => setTheme(applyTheme(k))}>{label}</button>
            ))}
          </div>
        </div>
        <div className="card about">
          <div className="eyebrow">Application</div>
          <div className="ver">{a.version ? "v" + a.version : "version inconnue"}</div>
          <div className="muted small">Si ce numéro n'est pas le dernier déployé, recharge la page en force ou ferme puis rouvre l'app.</div>
          <Row k="Interface chargée" v={"v" + (APP_VERSION || "?")} />
          <Row k="Règles de sécurité" v={a.registry ? `${a.registry} (${a.registry_hash})` : "n/d"} />
          <Row k="Fiches scientifiques" v={a.cards ?? "n/d"} />
          <Row k="Coach" v={a.llm || "n/d"} />
          <div className="row-actions"><button className="btn small" disabled={clBusy} onClick={() => void testClaude()}>{clBusy ? "Test en cours…" : "Tester Claude"}</button></div>
          {cl ? <ul id="claude-check" className="check-list small">{cl.steps.map((s) => (
            <li key={s.step} className={s.ok ? "good" : "warn-text"}>{`${s.ok ? "✓" : "✗"} ${s.step}${s.model ? ` · ${s.model}` : ""}${s.ms !== undefined ? ` · ${s.ms} ms` : ""} : ${s.message}`}
              {s.detail ? <div className="muted">{s.detail}</div> : null}</li>))}</ul> : null}
          <Row k="Dictée serveur" v={dict.server ? (dict.ready ? `prête (${dict.model})` : dict.loading ? "chargement du modèle…" : "installée, modèle à charger") : "indisponible (micro du navigateur)"} />
          <Row k="Image d'avatar (Gemini)" v={img.enabled ? `activée · ${img.today}/${img.per_day} aujourd'hui` : "désactivée (avatar dessiné)"} />
          {img.enabled ? <div className="row-actions"><button className="btn small" disabled={gemBusy} onClick={testGemini}>Tester la clé Gemini</button></div> : null}
          <div id="gem-result" className="muted small">{gem}</div>
          <Row k="Date du serveur" v={a.today || "n/d"} />
          {a.agents ? <AgentsPanel info={a.agents} /> : null}
        </div>
        <div className="card">
          <div className="eyebrow">Connexions</div>
          <div>{`Agenda : ${m.google ? "Google ✓" : "local"} · Strava : ${m.strava ? "✓" : "non connecté"}`}</div>
          <div className="row-actions"><button className="btn small" onClick={connectStrava}>{m.strava ? "Reconnecter Strava" : "Connecter Strava"}</button></div>
          <div className="muted">{`Coût Claude aujourd'hui : ${eur(m.spent_today_eur)} / ${eur(m.budget_eur)}`}</div>
          <div className="row-actions">
            {m.push_public_key ? <button className="btn small" onClick={() => void enablePush(m.push_public_key as string)}>Activer les notifications</button>
              : <span className="muted">Notifications non configurées sur le serveur</span>}
            <button className="btn small" onClick={onLogout}>Se déconnecter</button>
          </div>
          <details className="alt">
            <summary>Recommencer à zéro</summary>
            <p className="muted">Efface tes sorties, ton plan et la discussion, pour refaire le parcours d'accueil. Une sauvegarde complète est faite juste avant ; la connexion Strava est gardée.</p>
            <button className="btn small danger" data-armed={armed ? "1" : undefined} onClick={reset}>{armed ? "Confirmer : tout effacer" : "Tout effacer et recommencer"}</button>
          </details>
        </div>
      </> : null;
  if (embedded) return <div id="reglages" className="settings" hidden={!active}><h2 className="settings-title">Réglages</h2>{content}</div>;
  return <section id="view-details" className="view" data-view="details" hidden={!active}>{content}</section>;
}
