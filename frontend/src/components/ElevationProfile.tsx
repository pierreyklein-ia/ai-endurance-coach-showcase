/* v0.38 : profil d'altitude du simulateur (SVG, sans dépendance) : ravitos, points d'eau, barrières horaires.
   v0.39 : survol (souris ou doigt) = km, altitude et heure de passage sous le curseur ; toucher un point = sa fiche. */
import { useId, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactElement } from "react";
import type { RaceLeg, ScenarioId, SimPoint } from "../api/types";
import { clockAdd, elapsedAtKm, fmtHM, fmtLeg, fmtMargin, profileGeom, profileMarks, STATUS_LABEL } from "../lib/race";

const kmFr = (k: number): string => String(Math.round(k * 10) / 10).replace(".", ",");
const SCEN: [ScenarioId, string][] = [["ambitieux", "Ambitieux"], ["cible", "Cible"], ["prudent", "Prudent"]];

export function ElevationProfile({ profile, points, scenario, approx, legs = [] }: {
  profile: [number, number][]; points: SimPoint[]; scenario: ScenarioId; approx: boolean; legs?: RaceLeg[];
}): ReactElement | null {
  const svg = useRef<SVGSVGElement>(null);
  const gid = "elevg" + useId().replace(/:/g, "");
  const [hoverKm, setHoverKm] = useState<number | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  if (profile.length < 2) return null;
  const g = profileGeom(profile);
  const marks = profileMarks(points);
  const start = points[0]?.arrival[scenario].clock || "00:00";
  const top = points.reduce((a, p) => (p.alt !== null && (a === null || p.alt > (a.alt ?? 0)) ? p : a), null as SimPoint | null);
  const label = `Profil d'altitude${approx ? " approximatif" : ""} : ${points.length - 2} points de passage, `
    + `point culminant ${top ? `${top.name} (${top.alt} m, km ${String(top.km).replace(".", ",")})` : ""}. `
    + "Survole le profil pour l'heure de passage, touche un point pour sa fiche.";

  const move = (e: PointerEvent<SVGSVGElement>) => {
    const r = svg.current?.getBoundingClientRect();
    if (!r || !r.width) return;
    setHoverKm(g.kmAt(((e.clientX - r.left) / r.width) * g.W));
  };
  const leave = (e: PointerEvent<SVGSVGElement>) => { if (e.pointerType === "mouse") setHoverKm(null); };

  // curseur : km, altitude, heure de passage du scénario affiché
  let cursor: ReactElement | null = null, readout = "";
  if (hoverKm !== null) {
    const alt = Math.round(g.altAt(hoverKm)), el = elapsedAtKm(points, scenario, hoverKm);
    const x = g.x(hoverKm), y = g.y(alt), clock = clockAdd(start, el);
    readout = `km ${kmFr(hoverKm)} · ${alt} m · ${clock} (${fmtHM(el)} de course)`;
    const bw = 118, bx = Math.min(Math.max(x - bw / 2, g.L), g.W - g.R - bw);
    cursor = (
      <g className="elev-cursor" aria-hidden="true">
        <line x1={x} x2={x} y1={g.T - 4} y2={g.H - g.B} />
        <circle cx={x} cy={y} r={4.5} />
        <rect x={bx} y={0} width={bw} height={30} rx={6} />
        <text x={bx + bw / 2} y={12} textAnchor="middle" className="c1">{`${clock} · km ${kmFr(hoverKm)}`}</text>
        <text x={bx + bw / 2} y={25} textAnchor="middle" className="c2">{`${alt} m · ${fmtHM(el)}`}</text>
      </g>
    );
  }

  const open = (name: string) => setSel((s) => (s === name ? null : name));
  const key = (e: KeyboardEvent, name: string) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(name); } };
  const p = sel ? points.find((x) => x.name === sel) : undefined;
  const leg = p ? legs.find((l) => l.from === p.name) : undefined;
  const legIn = p ? legs.find((l) => l.to === p.name) : undefined;

  return (
    <figure className="elev">
      <svg ref={svg} className="elev-svg interactive" viewBox={`0 0 ${g.W} ${g.H}`} role="img" aria-label={label}
        onPointerMove={move} onPointerDown={move} onPointerLeave={leave}>
        <defs>
          <linearGradient id={gid} className="elev-grad" x1="0" y1="0" x2="0" y2="1">
            <stop className="s0" offset="0%" /><stop className="s1" offset="100%" />
          </linearGradient>
        </defs>
        {g.yTicks.map((v) => <g key={`y${v}`}><line className="grid" x1={g.L} x2={g.W - g.R} y1={g.y(v)} y2={g.y(v)} />
          <text className="tick" x={g.L - 6} y={g.y(v) + 4} textAnchor="end">{v}</text></g>)}
        {g.xTicks.map((v) => <text key={`x${v}`} className="tick" x={g.x(v)} y={g.H - 6} textAnchor="middle">{v}</text>)}
        <path className={"elev-area" + (approx ? " approx" : "")} d={g.area} fill={`url(#${gid})`} />
        <path className={"elev-line" + (approx ? " approx" : "")} d={g.line} />
        {top && top.alt !== null ? (            // v0.52.5 : point culminant annoté
          <g className="elev-top" aria-hidden="true">
            <text x={Math.min(Math.max(g.x(top.km), g.L + 24), g.W - g.R - 24)} y={g.y(top.alt) - 8} textAnchor="middle">
              {`${top.alt.toLocaleString("fr-FR")} m`}</text>
          </g>) : null}
        {cursor}
        {marks.map((m) => {
          const x = g.x(m.km), y = g.y(m.alt ?? g.altAt(m.km));
          const cut = m.cutoff;
          return (
            <g key={m.name + m.km} className={"elev-mark " + m.kind + (cut ? ` cut ${cut.status}` : "") + (sel === m.name ? " sel" : "")}
              role="button" tabIndex={0} aria-label={`${m.name}, km ${kmFr(m.km)}, passage ${m.arrival[scenario].clock} : voir la fiche`}
              aria-pressed={sel === m.name} onClick={(e) => { e.stopPropagation(); open(m.name); }} onKeyDown={(e) => key(e, m.name)}>
              <line className="drop" x1={x} x2={x} y1={y + 4} y2={g.H - g.B} />
              <circle className="hit" cx={x} cy={y} r={14} />
              {m.kind === "ravito" ? <circle className="dot" cx={x} cy={y} r={4.5} />
                : m.kind === "eau" ? <circle className="dot" cx={x} cy={y} r={3} />
                : <path className="dot" d={`M${x - 4} ${y - 8} L${x + 4} ${y - 8} L${x} ${y - 2} Z`} />}
              {cut ? <rect className="cutmark" x={x - 3} y={g.H - g.B - 7} width={6} height={6} rx={1.5}
                transform={`rotate(45 ${x} ${g.H - g.B - 4})`} /> : null}
            </g>
          );
        })}
        <text className="tick" x={g.L} y={11}>m</text>
        <text className="tick" x={g.W - g.R} y={g.H - 6} textAnchor="end">km</text>
      </svg>
      <div className="elev-readout small" aria-live="polite">{readout || "Survole ou fais glisser ton doigt sur le profil : heure de passage à chaque kilomètre."}</div>
      <figcaption className="elev-legend small muted">
        <span><i className="lg ravito" aria-hidden="true" />ravito</span>
        <span><i className="lg eau" aria-hidden="true" />point d'eau</span>
        <span><i className="lg cut" aria-hidden="true" />barrière horaire</span>
        {approx ? <span>tracé en pointillés : profil approximatif</span> : null}
      </figcaption>
      {p ? (
        <div className="elev-info" role="region" aria-label={`Fiche : ${p.name}`}>
          <div className="elev-info-head"><b>{p.name}</b>
            <button type="button" className="btn small" onClick={() => setSel(null)} aria-label="Fermer la fiche">✕</button></div>
          <div className="small muted">{`km ${kmFr(p.km)}` + (p.alt !== null ? ` · ${p.alt} m${p.alt_approx ? " (≈)" : ""}` : "")
            + (p.stop_min ? ` · arrêt prévu ${p.stop_min} min` : "") + (p.note ? ` · ${p.note}` : "")}</div>
          <div className="elev-info-times">
            {SCEN.map(([id, lb]) => <div key={id} className={id === scenario ? "on" : ""}><small>{lb}</small><b>{p.arrival[id].clock}</b><small>{fmtHM(p.arrival[id].s)}</small></div>)}
          </div>
          {p.cutoff ? <div className={"small cut-" + p.cutoff.status}>{`Barrière ${p.cutoff.clock}${p.cutoff.approx ? " (≈, édition " + (p.cutoff.edition ?? "?") + ")" : ""} : marge ${fmtMargin(p.cutoff.margin_min[scenario])} (${STATUS_LABEL[p.cutoff.status]}).`}</div> : null}
          {legIn ? <div className="small">{`Pour y arriver : ${fmtLeg(legIn.duration_s)} depuis ${legIn.from === points[0].name ? "le départ" : legIn.from}, ${legIn.carbs_g} g de glucides.`}</div> : null}
          {leg?.at_aid.length ? <div className="small"><span className="lbl">Au ravito :</span>{` ${leg.at_aid_label}.`}</div> : null}
          {leg ? <div className="small"><span className="lbl">Pour repartir :</span>{` ${leg.carry_label || "rien de plus"}, ${leg.flasks} × 500 ml, jusqu'à ${leg.to} (${fmtLeg(leg.duration_s)}).`}</div> : null}
        </div>
      ) : null}
    </figure>
  );
}
