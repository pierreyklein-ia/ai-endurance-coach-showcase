/* v0.36 : choix du coach (premier lancement et Carrière › Réglages) : genre, personnalité, prénom, tu / vous, aperçu en direct */
import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { CoachPersona } from "../api/types";
import { cleanName, coachOf, personalityLabel, PERSONALITIES, withGender } from "../lib/coach";
import { toast } from "../lib/toast";
import { CoachAvatar } from "./CoachAvatar";

export function CoachPicker({ value, submitLabel, onSaved }: {
  value: CoachPersona | null | undefined;
  submitLabel: string;
  onSaved: (c: CoachPersona) => void;
}): ReactElement {
  const [c, setC] = useState<CoachPersona>(() => coachOf(value));
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (value) setC(value); }, [value]);
  const name = cleanName(c.name);
  const save = async () => {
    if (!name) { toast("Donne un prénom à ton coach"); return; }
    setBusy(true);
    try {
      const r = await api<CoachPersona>("/api/coach", { json: { gender: c.gender, personality: c.personality, name, address: c.address,
        vocabulary: c.vocabulary || "simple" } });
      onSaved(r);
    } catch (err) { toast((err as Error).message, 5000); }
    setBusy(false);
  };
  const fem = c.gender === "femme";
  return (
    <div className="coach-pick">
      <div className="coach-pick-preview">
        <CoachAvatar gender={c.gender} personality={c.personality} size={120} title={`Avatar de ${name || "ton coach"}`} />
        <div>
          <div className="coach-pick-name">{name || "…"}</div>
          <div className="muted small">{`${personalityLabel(c.personality, c.gender)} · ${c.address === "vous" ? "vouvoiement" : "tutoiement"}`}</div>
          <div className="coach-pick-quote">{quote(c, name)}</div>
        </div>
      </div>
      <div className="eyebrow">Coach</div>
      <div className="seg" role="group" aria-label="Genre du coach">
        <button type="button" className={fem ? "on" : undefined} aria-pressed={fem} onClick={() => setC(withGender(c, "femme"))}>Femme</button>
        <button type="button" className={!fem ? "on" : undefined} aria-pressed={!fem} onClick={() => setC(withGender(c, "homme"))}>Homme</button>
      </div>
      <div className="eyebrow">Personnalité</div>
      <div className="coach-pick-grid" role="radiogroup" aria-label="Personnalité du coach">
        {PERSONALITIES.map((p) => (
          <button key={p.key} type="button" role="radio" aria-checked={c.personality === p.key}
            className={"coach-pick-card" + (c.personality === p.key ? " on" : "")} onClick={() => setC({ ...c, personality: p.key })}>
            <CoachAvatar gender={c.gender} personality={p.key} size={36} head />
            <span><b>{personalityLabel(p.key, c.gender)}</b><span className="muted small">{p.desc}</span></span>
          </button>
        ))}
      </div>
      <label className="eyebrow" htmlFor="coach-name">Prénom</label>
      <input id="coach-name" className="coach-pick-input" type="text" maxLength={20} autoComplete="off" value={c.name}
        onChange={(e) => setC({ ...c, name: e.target.value })} />
      <div className="eyebrow">{fem ? "Elle te parle…" : "Il te parle…"}</div>
      <div className="seg" role="group" aria-label="Tutoiement ou vouvoiement">
        <button type="button" className={c.address === "tu" ? "on" : undefined} aria-pressed={c.address === "tu"} onClick={() => setC({ ...c, address: "tu" })}>en tutoyant</button>
        <button type="button" className={c.address === "vous" ? "on" : undefined} aria-pressed={c.address === "vous"} onClick={() => setC({ ...c, address: "vous" })}>en vouvoyant</button>
      </div>
      <div className="eyebrow">Vocabulaire</div>
      <div className="seg" role="group" aria-label="Vocabulaire du coach">
        <button type="button" className={c.vocabulary !== "expert" ? "on" : undefined} aria-pressed={c.vocabulary !== "expert"} onClick={() => setC({ ...c, vocabulary: "simple" })}>simple</button>
        <button type="button" className={c.vocabulary === "expert" ? "on" : undefined} aria-pressed={c.vocabulary === "expert"} onClick={() => setC({ ...c, vocabulary: "expert" })}>expert</button>
      </div>
      <p className="muted small">{c.vocabulary === "expert" ? "Termes techniques (VMA, seuil, 30/30…) sans explication." : "Les termes techniques (VMA, seuil…) sont expliqués en mots simples, avec un « C'est quoi ? » sur chaque séance."}</p>
      <p className="muted small">La personnalité change le ton des messages, pas la sécurité : les garde-fous et le calcul du plan restent les mêmes.</p>
      <div className="row-actions"><button type="button" className="btn primary" disabled={busy || !name} onClick={() => void save()}>{busy ? "Un instant…" : submitLabel}</button></div>
    </div>
  );
}

function quote(c: CoachPersona, name: string): string {
  return quoteText(c, name).replace("« ", "«\u00a0").replace(" »", "\u00a0»").replace(/ ([!?:])/g, "\u00a0$1");
}

function quoteText(c: CoachPersona, name: string): string {
  const v = c.address === "vous", fem = c.gender === "femme", n = name || "ton coach";
  switch (c.personality) {
    case "exigeant": return v ? `« ${n}. Vous voulez progresser ? Alors on fait chaque séance comme prévu. »` : `« ${n}. Tu veux progresser ? Alors on fait chaque séance comme prévu. »`;
    case "motivateur": return v ? `« C'est ${n} ! Allez, on va chercher votre objectif ensemble ! »` : `« C'est ${n} ! Allez, on va chercher ton objectif ensemble ! »`;
    case "sante": return v ? `« Je suis ${n}. Votre corps d'abord : on progresse sans se blesser. »` : `« Je suis ${n}. Ton corps d'abord : on progresse sans se blesser. »`;
    default: return v ? `« Bonjour, je suis ${n}, ${fem ? "ravie" : "ravi"} de vous accompagner ! »` : `« Salut, moi c'est ${n}, ${fem ? "ravie" : "ravi"} de t'accompagner ! »`;
  }
}
