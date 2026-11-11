/* v0.50 : « Mes zones » (Carrière › Réglages) : FC max, repos, seuil (avec leur source), 5 zones, allures repères. */
import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { StravaConnect, ZoneValue, ZonesView } from "../api/types";
import { toast } from "../lib/toast";
import { parseBpm, sourceChip, targetLine, zoneRange, zonesError } from "../lib/zones";

const FIELDS: [keyof Pick<ZonesView, "hr_max" | "hr_rest" | "lthr">, string, string][] = [
  ["hr_max", "FC max", "Fréquence cardiaque maximale"], ["hr_rest", "FC repos", "Fréquence cardiaque de repos"],
  ["lthr", "FC seuil", "Fréquence cardiaque au seuil lactique"]];

function Val({ v }: { v: ZoneValue }): ReactElement {
  return <span className="zv"><b>{v.value}</b><span className="small muted"> bpm</span> <span className={`src src-${v.source_id}`}>{sourceChip(v)}</span></span>;
}

export function ZonesCard({ active, nonce }: { active: boolean; nonce: number }): ReactElement | null {
  const [v, setV] = useState<ZonesView | null>(null);
  const [gen, setGen] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<ZonesView>("/api/zones").then((r) => { if (!stale) { setV(r); setGen((g) => g + 1); } }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce]);
  if (!v) return null;

  const send = async (json: Record<string, number | null>) => {
    setBusy(true);
    try {
      const r = await api<ZonesView>("/api/zones", { json });
      setV(r); setGen((g) => g + 1); setErr("");
      toast("Zones enregistrées ✓ : elles servent aux prochaines séances importées et aux cibles du plan", 5000);
    } catch (er) { setErr((er as Error).message); }
    setBusy(false);
  };
  const save = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const form = { hr_max: parseBpm(String(f.get("hr_max") || "")), hr_rest: parseBpm(String(f.get("hr_rest") || "")), lthr: parseBpm(String(f.get("lthr") || "")) };
    const bad = zonesError(form, { hr_max: v.hr_max.auto, hr_rest: v.hr_rest.auto, lthr: v.lthr.auto });
    setErr(bad || "");
    if (bad) return;
    await send(form);                           // champ vide = automatique (Strava, estimation ou réglage)
  };
  const reconnect = async () => {
    try {
      const r = await api<StravaConnect>("/api/strava/connect", { json: { origin: location.origin, force: true } });
      if (r.status === "redirect" && r.url) location.href = r.url; else toast(r.message || "Strava connecté ✓");
    } catch (er) { toast((er as Error).message); }
  };

  return (
    <div className="card zones-card" id="mes-zones">
      <div className="eyebrow">Mes zones</div>
      <div className="zones-vals">
        {FIELDS.map(([k, lbl]) => <div key={k} className="zone-val"><span className="small muted">{lbl}</span><Val v={v[k]} /></div>)}
      </div>
      <table className="zones-table">
        <caption className="small muted">{`Zones FC · modèle ${v.model}`}</caption>
        <tbody>{v.zones.map((z) => (
          <tr key={z.zone} className={`z-${z.zone.toLowerCase()}`}><th scope="row"><span className="tgt-zone">{z.zone}</span></th>
            <td>{z.label}</td><td className="num">{zoneRange(z.lo, z.hi)} bpm</td><td className="small muted num">{z.pct}</td></tr>
        ))}</tbody>
      </table>
      {v.paces.length ? <div className="zones-paces">{v.paces.map((p) => <span key={p.key} className="pace-chip"><span className="small muted">{p.label}</span> <b>{p.value}</b></span>)}</div>
        : <div className="muted small">Allures repères : pas encore de record exploitable (un 5 à 10 km à fond suffit).</div>}
      <details className="zones-targets"><summary>Cibles par type de séance</summary>
        <ul className="small">{v.targets.map((t) => <li key={t.intensity}><b>{t.label}</b> : {targetLine(t)}</li>)}</ul>
      </details>
      {v.strava.needs_reconnect ? (
        <div className="note small">Pour lire tes zones FC sur Strava, il faut une autorisation de plus (profil). Reconnecte Strava : tes données restent, rien n'est perdu.
          <div className="row-actions"><button className="btn small" onClick={reconnect}>Reconnecter Strava</button></div></div>
      ) : v.strava.zones ? <div className="muted small">{`Zones Strava${v.strava.custom ? " (personnalisées)" : ""} : ${v.strava.zones.map((z) => z.max > 0 ? `${z.min}-${z.max}` : `${z.min}+`).join(" · ")}`}</div> : null}
      <form key={gen} className="wm-form zones-form" onSubmit={save} noValidate>
        {FIELDS.map(([k, lbl, aria]) => (
          <label key={k} className="wm-f"><span className="small">{lbl}</span>
            <input name={k} inputMode="numeric" placeholder={`auto : ${v[k].auto}`} defaultValue={v[k].override ?? ""} aria-label={`${aria} (bpm), vide = automatique`} /></label>
        ))}
        <button className="btn small primary" type="submit" disabled={busy}>{busy ? "…" : "Enregistrer"}</button>
      </form>
      {err ? <div className="error small" role="alert">{err}</div>
        : <div className="muted small">Laisse vide pour l'automatique : Strava, sinon estimé d'après tes sorties (FC max observée, meilleurs efforts de 30 à 60 min), sinon 93 % de la FC max pour le seuil.</div>}
    </div>
  );
}
