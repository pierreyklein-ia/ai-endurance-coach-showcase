/* carrière (v0.24) : XP, niveaux, avatar, trophées, frise, équipement, charges */
import { useEffect, useRef, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import { api } from "../api/client";
import type { CareerEvent, Career as CareerT } from "../api/types";
import { AvatarPic } from "../components/AvatarSvg";
import { Medal } from "../components/Medal";
import { TIER } from "../lib/avatar";
import { familyOrder, nextTrophies } from "../lib/careerUi";
import { dayLabel, frDateTime, frInt as fr } from "../lib/format";
import { toast } from "../lib/toast";

const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];

/** v0.33 : texte d'un événement proche (« dans 6 jours », « aujourd'hui », progression du calendrier de l'Avent…) */
export function eventLine(e: CareerEvent): string {
  const when = e.live ? (e.need > 1 ? `en cours : ${e.count}/${e.need} jours` : "c'est aujourd'hui !") : e.days_left === 1 ? "demain" : `dans ${e.days_left} jours`;
  const state = e.done ? "réussi cette année ✓" : e.earned ? "déjà gagné, à refaire pour le plaisir" : `+${e.xp} XP`;
  return `${e.name} · ${when} · ${state}`;
}

export function Career({ active, nonce, onCareer, onSeen, onEditAvatar, settings }: {
  active: boolean; nonce: number;
  /** nouvelle carrière chargée : la barre du haut suit */
  onCareer: (c: CareerT) => void;
  /** nouveautés vues : plus de pastille sur l'avatar du haut */
  onSeen: () => void;
  onEditAvatar: () => void;
  /** v0.39 : réglages de l'app (ex-onglet Détails), en bas de la page */
  settings?: ReactNode;
}): ReactElement {
  const [c, setC] = useState<CareerT | null>(null);
  const [reload, setReload] = useState(0);
  const [gen, setGen] = useState(0);       // chaque chargement repart d'un formulaire de charges vide
  const activeRef = useRef(active);
  activeRef.current = active;

  useEffect(() => {
    if (!active) return;
    let stale = false;
    api<CareerT>("/api/career").then((r) => {
      if (stale) return;
      onCareer(r);
      if (!activeRef.current) return;
      setC(r); setGen((g) => g + 1);
      if (r.first_visit || r.new?.length || r.level_up) api("/api/career/seen", { method: "POST" }).then(onSeen).catch(() => {});
    }).catch(() => {});
    return () => { stale = true; };
  }, [active, nonce, reload]);

  // image Gemini en préparation : on revient voir dans 25 s
  useEffect(() => {
    if (!c?.avatar_image?.generating) return;
    const t = setTimeout(() => {
      if (activeRef.current) setReload((n) => n + 1);
      else api<CareerT>("/api/career").then(onCareer).catch(() => {});
    }, 25000);
    return () => clearTimeout(t);
  }, [c]);

  const addStrength = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      await api("/api/career/strength", { json: { exercise: f.get("exercise"), kg: +(f.get("kg") as string), reps: +(f.get("reps") as string), bodyweight: +(f.get("bodyweight") as string) } });
      toast("Charge enregistrée ✓"); setReload((n) => n + 1);
    } catch (err) { toast((err as Error).message); }
  };
  const saveBirthday = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const day = +(f.get("day") as string), month = +(f.get("month") as string);
    try {
      await api("/api/career/birthday", { json: day && month ? { day, month } : {} });
      toast(day && month ? "Anniversaire enregistré ✓" : "Anniversaire effacé"); setReload((n) => n + 1);
    } catch (err) { toast((err as Error).message); }
  };
  const uploadFace = async (file: File | undefined) => {
    if (!file) return;
    try {
      toast("Envoi de la photo…", 8000);
      await api("/api/career/face", { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
      toast("Photo enregistrée ✓ : nouvelle image en préparation"); setReload((n) => n + 1);
    } catch (err) { toast((err as Error).message); }
  };
  const deleteFace = async () => {
    try { await api("/api/career/face", { method: "DELETE" }); toast("Photo supprimée du serveur"); setReload((n) => n + 1); }
    catch (err) { toast((err as Error).message); }
  };
  const regenerate = async () => {
    try { await api("/api/career/avatar/regenerate", { method: "POST" }); toast("Nouvelle image en préparation…"); setReload((n) => n + 1); }
    catch (err) { toast((err as Error).message); }
  };

  // v0.39 : les réglages (ex-onglet Détails) sont en bas de cette page
  const toSettings = () => document.getElementById("reglages")?.scrollIntoView({ behavior: "smooth", block: "start" });
  useEffect(() => {
    if (!active || !c || !/^#(details|reglages)$/.test(location.hash)) return;
    const t = setTimeout(toSettings, 350);                  // ancienne adresse #details : on descend aux réglages
    return () => clearTimeout(t);
  }, [active, c]);

  let body: ReactNode = null;
  if (c) {
    const EYE_LEVEL = c.eye_level ?? 7;     // v0.40 : niveau de l'Œil du coach (coach/agents/eye.py)
    const newIds = new Set((c.new || []).map((t) => t.id));
    const img = c.avatar_image;
    const banners: ReactNode[] = [];
    if (c.first_visit) banners.push(<div key="fv" className="card banner"><b>Ta carrière est reconstruite ! </b>{`J'ai relu ton historique : ${c.count} trophées, ${fr(c.xp)} XP, niveau ${c.level} d'un coup. La suite se gagne à l'entraînement.`}</div>);
    else if (c.level_up) {
      const g = c.gear.find((x) => x.level === c.level);
      banners.push(<div key="lu" className="card banner pop"><b>{`Niveau ${c.level} ! `}</b>{`Tu es ${c.rank.toLowerCase()}.` + (g ? ` Nouvel équipement : ${g.label.toLowerCase()}.` : "")}</div>);
    }
    if (c.new?.length) banners.push(<div key="nw" className="card banner pop"><b>{c.new.length > 1 ? `${c.new.length} nouveaux trophées : ` : "Nouveau trophée : "}</b>{c.new.slice(0, 4).map((t) => `${t.label} (+${t.xp} XP)`).join(", ")}</div>);
    // prochains objectifs : le plus petit palier encore à gagner dans chaque famille
    const order = familyOrder(c);
    const bday = c.birthday ? c.birthday.split("-").map(Number) : null;
    const next = nextTrophies(c);
    const events = [...c.levels.map((l) => ({ day: l.day, text: `Niveau ${l.level} · ${l.rank}`, lvl: true, xp: 0 })), ...c.timeline.map((t) => ({ day: t.day, text: `${t.label}`, xp: t.xp, lvl: false }))]
      .sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : a.lvl ? -1 : 1)).slice(0, 25);
    body = <>
      <div className="career-top"><button type="button" className="btn small settings-jump" onClick={toSettings}>⚙ Réglages</button></div>
      {banners}
      <div className={"card career-hero" + (c.level_up ? " levelup" : "")}>
        <div className="hero-av">
          <AvatarPic c={c} size={150} head={false} />
          {img?.generating ? <div className="muted small">✨ Nouvelle image en préparation…</div> : null}
          {img?.error && !img?.generating ? <div className="muted small">{img.error}</div> : null}
        </div>
        <div className="hero-txt">
          <div className="lvl-big">{`Niveau ${c.level}`}</div><div className="rank">{c.rank}</div>
          <div className="xpbar" role="progressbar" aria-valuenow={c.pct} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.max(2, c.pct)}%` }}></i></div>
          <div className="muted small">{`${fr(c.xp)} XP · encore ${fr(c.next_at - c.xp)} XP pour le niveau ${c.level + 1}`}</div>
          <div className="muted small">{`${c.count} trophées sur ${c.total}`}</div>
          <a className="muted small eye-reward" href="#progres">{c.level >= EYE_LEVEL ? "👁 Œil du coach débloqué : analyse par toute l'équipe (Progrès)"
            : `🔒 Niveau ${EYE_LEVEL} : l'Œil du coach analyse tout ton entraînement avec toute l'équipe d'agents`}</a>
          <button className="btn small" onClick={onEditAvatar}>{c.avatar_set ? "Modifier mon avatar" : "Créer mon avatar"}</button>
          {img?.next_at && !img?.generating ? <div className="muted small">{`Nouvelle image possible le ${frDateTime(img.next_at)}`}</div> : null}
          {img?.enabled && img?.ready && !img?.generating && !img?.next_at ? <button className="btn small" onClick={regenerate}>Nouvelle image</button> : null}
        </div>
      </div>
      {c.events?.length ? (
        <div className="card banner"><h3>Événements</h3>
          {c.events.map((e) => (
            <div key={e.id + e.start} className="goal-row"><Medal tier={e.need > 1 ? 2 : 1} done={e.done} />
              <div><div className="t">{e.label}</div><div className="muted small">{eventLine(e)}</div><div className="muted small">{e.desc}</div></div>
              <span className="xp">{e.earned ? "✓" : `+${e.xp} XP`}</span></div>
          ))}
        </div>
      ) : null}
      <div className="card"><h3>Prochains objectifs</h3>
        {next.map((t) => (
          <div key={t.id} className="goal-row"><Medal tier={t.tier} done={false} />
            <div><div className="t">{t.label}</div><div className="muted small">{t.progress || t.desc}</div></div>
            <span className="xp">{`+${t.xp} XP`}</span></div>
        ))}
      </div>
      {order.map((f) => {
        const label = c.families[f];
        const list = c.trophies.filter((t) => t.family === f);
        const n = list.filter((t) => t.day).length;
        return (
          <details key={f} className="card fam" open={f === "jalons" ? true : undefined}>
            <summary><b>{label}</b><span className="muted small">{` ${n}/${list.length}`}</span></summary>
            <div className="tro-grid">
              {list.map((t) => (
                <div key={t.id} className={"tro" + (t.day ? " on" : "") + (newIds.has(t.id) ? " new" : "")} title={t.desc}>
                  <Medal tier={t.tier} done={!!t.day} />
                  <div className="t">{t.label}</div>
                  <div className="muted small">{t.day ? dayLabel(t.day) + " " + t.day.slice(0, 4) : (t.progress || TIER[t.tier][0])}</div>
                  <div className="xp">{`${t.day ? "" : "+"}${t.xp} XP`}</div>
                </div>
              ))}
            </div>
          </details>
        );
      })}
      <details className="card"><summary><b>Ta frise</b></summary>
        <ol className="frise">{events.map((e, i) => (
          <li key={i} className={e.lvl ? "lvl" : ""}><span className="muted small">{`${dayLabel(e.day)} ${e.day.slice(0, 4)}`}</span><span>{e.text}</span>{e.xp ? <span className="xp">{`+${e.xp}`}</span> : null}</li>
        ))}</ol>
      </details>
      {img?.enabled ? (
        <div className="card"><h3>Ma photo</h3>
          {c.level >= (c.face_level ?? 10) ? <>
            <div className="muted small">Envoie une photo de ton visage : Gemini dessine un avatar qui te ressemble, toujours en style cartoon.
              La photo reste sur ton serveur (jamais dans les sauvegardes) et n'est envoyée qu'à Google, au moment de dessiner l'image.</div>
            <div className="row-actions">
              <label className="btn small primary">{img.face ? "Changer de photo" : "Envoyer une photo"}
                <input type="file" accept="image/jpeg,image/png,image/webp,image/heic" hidden onChange={(e) => uploadFace(e.target.files?.[0])} /></label>
              {img.face ? <button className="btn small danger" onClick={deleteFace}>Supprimer ma photo</button> : null}
            </div>
          </> : <div className="muted small">{`🔒 Au niveau ${c.face_level ?? 10}, tu pourras envoyer une photo de ton visage pour un avatar IA qui te ressemble.`}</div>}
        </div>
      ) : null}
      <div className="card"><h3>Mon anniversaire</h3>
        <div className="muted small">Le jour et le mois seulement : un trophée si tu cours ce jour-là, et un chapeau de fête pour ton avatar autour de la date.</div>
        <form key={"b" + gen} className="strength-form" onSubmit={saveBirthday}>
          <input name="day" type="number" inputMode="numeric" min={1} max={31} placeholder="Jour" aria-label="Jour" defaultValue={bday ? bday[1] : ""} />
          <select name="month" aria-label="Mois" defaultValue={bday ? String(bday[0]) : ""}>
            <option value="">Mois</option>
            {MOIS.map((m, i) => <option key={m} value={String(i + 1)}>{m}</option>)}
          </select>
          <button className="btn small primary" type="submit">Enregistrer</button>
        </form>
      </div>
      <div className="card"><h3>Mes charges</h3>
        <div className="muted small">Strava n'enregistre pas les charges : note ta meilleure série (ex. 170 kg × 5). Le maximum est estimé (formule d'Epley).</div>
        <form key={gen} className="strength-form" onSubmit={addStrength}>
          <select name="exercise" aria-label="Exercice">
            {[["box_squat", "Box squat"], ["squat", "Squat"], ["trap_bar", "Trap bar"], ["deadlift", "Soulevé de terre"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <input name="kg" type="number" inputMode="decimal" min={1} max={500} step="0.5" placeholder="Charge (kg)" required />
          <input name="reps" type="number" inputMode="numeric" min={1} max={12} defaultValue={5} aria-label="Répétitions" required />
          <input name="bodyweight" type="number" inputMode="decimal" min={20} max={200} step="0.1" placeholder="Ton poids (kg)" required defaultValue={c.strength_log?.[0]?.bodyweight || ""} />
          <button className="btn small primary" type="submit">Ajouter</button>
        </form>
        {(c.strength_log || []).slice(0, 5).map((s, i) => (
          <div key={i} className="muted small">{`${dayLabel(s.day)} · ${s.exercise.replace("_", " ")} ${s.kg} kg × ${s.reps} → max ~${s.one_rm} kg (${(s.one_rm / s.bodyweight).toFixed(2).replace(".", ",")} × ton poids)`}</div>
        ))}
      </div>
      <details className="card"><summary><b>Les règles du jeu</b></summary>
        <ul className="small">
          <li>Chaque trophée rapporte de l'XP une seule fois. Une séance du plan validée : +3 XP, +6 pour une séance clé (seuil, fractionné, sortie longue).</li>
          <li>{`Niveaux : 380 XP pour le niveau 2, puis chaque niveau demande 58 XP de plus que le précédent, sans niveau maximum : au-delà du 20, une étoile de plus par niveau. Au niveau ${EYE_LEVEL}, l'Œil du coach (toute l'équipe d'agents analyse tes 8 dernières semaines, une fois par mois) ; au niveau 10, la photo pour un avatar IA ; au niveau 20, le sac d'ultra. Prochains paliers : ${c.next_levels.map((l) => `niv. ${l.level} à ${fr(l.at)} XP`).join(", ")}.`}</li>
          <li>Séance bonus facultative faite : +4 XP. Sautée : rien ne se perd.</li>
          <li>Rien n'est gagné en faisant plus que le plan, et la régularité se compte en semaines, pas en jours d'affilée : le repos ne fait jamais perdre.</li>
          <li>Les chronos ne comptent que sur une sortie entière, à plat, de la bonne distance.</li>
        </ul>
      </details>
    </>;
  }
  return <section id="view-carriere" className="view" data-view="carriere" hidden={!active}>{body}{settings}</section>;
}
