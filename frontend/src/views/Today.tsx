import { useEffect, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { Career, Me, Session, TodayView } from "../api/types";
import { ActRow } from "../components/ActRow";
import { AvatarPic } from "../components/AvatarSvg";
import { Medal } from "../components/Medal";
import { Metric } from "../components/Metric";
import { SessionCard } from "../components/SessionCard";
import { careerLabel, nextTrophies, xpProgress } from "../lib/careerUi";
import { comma, dayLabel, fmtMin, frInt, signed } from "../lib/format";
import { eventLine } from "./Career";

/** v0.49 : carte compacte « Ma carrière » (sous la séance du jour) : niveau, XP, 2 trophées à portée, événement. */
export function CareerCard({ c }: { c: Career }): ReactElement {
  const { pct, toNext, next } = xpProgress(c);
  const tro = nextTrophies(c, 2);
  const ev = (c.events || []).filter((e) => e.live || e.days_left <= 7).slice(0, 1);
  return (
    <a id="career-card" className="card career-card" href="#carriere" aria-label={careerLabel(c) + " Ouvrir ma carrière."}>
      <span className="cc-av" aria-hidden="true"><AvatarPic c={c} size={52} head /></span>
      <span className="cc-main">
        <span className="cc-head"><span className="eyebrow">Ma carrière</span><span className="cc-lvl">{`Niveau ${c.level}`}</span><span className="rank">{c.rank}</span></span>
        <span className="xpbar" aria-hidden="true"><i style={{ width: `${Math.max(2, pct)}%` }}></i></span>
        <span className="muted small">{`${frInt(toNext)} XP avant le niveau ${next}`}</span>
        {tro.map((t) => (
          <span key={t.id} className="cc-tro"><Medal tier={t.tier} done={false} /><span className="cc-t">{t.label}</span>
            <span className="muted small cc-p">{t.progress || t.desc}</span><span className="xp">{`+${t.xp}`}</span></span>
        ))}
        {ev.map((e) => <span key={e.id + e.start} className="cc-ev small"><b>{e.label}</b>{` · ${eventLine(e)}`}</span>)}
      </span>
      <span className="cc-go" aria-hidden="true">›</span>
    </a>
  );
}

export function Today({ active, nonce, me, onHeader, onFeedback }: {
  active: boolean; nonce: number; me: Me | null; onHeader: (t: TodayView) => void; onFeedback: (s: Session) => void;
}): ReactElement {
  const [t, setT] = useState<TodayView | null>(null);
  const [career, setCareer] = useState<Career | null>(null);
  const [reload, setReload] = useState(0);              // v0.44 : après un lien avec une sortie
  const onboarding = !!me?.onboarding;
  useEffect(() => {
    if (!active || onboarding) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    api<TodayView>("/api/today").then((r) => {
      if (stale) return;
      onHeader(r); setT(r);
      // v0.52.3 : synchro Strava en cours (ouverture de l'appli) : on relit la vue quand elle a eu le temps de finir
      if (r.syncing && reload < 4) timer = setTimeout(() => setReload((n) => n + 1), 15000);
    }).catch(() => {});
    // v0.33 : événement du calendrier proche (la carrière est en cache côté serveur : appel peu coûteux)
    // v0.49 : et la carte « Ma carrière »
    api<Career>("/api/career").then((c) => { if (!stale) setCareer(c); }).catch(() => {});
    return () => { stale = true; if (timer) clearTimeout(timer); };
  }, [active, nonce, onboarding, reload]);

  let body: ReactElement | null = null;
  if (onboarding) {
    body = (
      <div className="card">
        <div className="eyebrow">Ton plan arrive</div>
        <p>On le construit ensemble dans le chat : réponds au coach, il ajuste la proposition jusqu'à ce qu'elle te convienne.</p>
        <a className="btn primary" href="#coach">Aller au chat</a>
      </div>
    );
  } else if (t) {
    const st = t.state;
    body = <>
      {t.focus ? <div className="card"><div className="eyebrow">Cette semaine</div><div>{t.focus}</div>{t.coach_note ? <div className="note">{t.coach_note}</div> : null}</div> : null}
      {t.sessions.length ? t.sessions.map((s) => <SessionCard key={s.session_id} s={s} onFeedback={onFeedback} today={t.today} onChanged={() => setReload((n) => n + 1)} />)
        : <div className="card empty">Pas de séance prévue aujourd'hui. Repos ✌︎</div>}
      {career ? <CareerCard c={career} /> : null}
      {t.activities.length ? <div className="card"><div className="eyebrow">Enregistré aujourd'hui</div>{t.activities.map((a, i) => <ActRow key={i} a={a} />)}</div> : null}
      {t.next ? (
        <div className="card">
          <div className="eyebrow">Prochaine séance</div>
          <div className="session-title">{t.next.title}</div>
          <div className="session-meta">{`${dayLabel(t.next.day)} · ${fmtMin(t.next.duration_min)}`}</div>
        </div>
      ) : null}
      <div className="metrics">
        <Metric k="Fraîcheur (TSB)" v={signed(st.tsb)} hint="forme − fatigue" />
        <Metric k="ACWR" v={st.acwr === null ? "n/d" : comma(st.acwr)} hint="aigu / chronique"
          cls={st.acwr !== null && st.acwr > 1.7 ? "bad" : st.acwr !== null && st.acwr > 1.5 ? "warn" : ""} />
      </div>
    </>;
  }
  return <section id="view-aujourdhui" className="view" data-view="aujourdhui" hidden={!active}>{body}</section>;
}
