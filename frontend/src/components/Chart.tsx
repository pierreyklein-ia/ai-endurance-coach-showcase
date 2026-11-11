/* Graphiques SVG sans dépendance (fonctionnent hors ligne). Points : [iso, valeur] ; null = trou. */
import { useRef, useState, type PointerEvent, type ReactElement, type ReactNode } from "react";
import { dayLabel, fr1, MOIS, niceTicks, tms } from "../lib/format";
import type { Point } from "../api/types";

export interface Series {
  label: string;
  color: "accent" | "warn" | "ink";
  points: Point[];
  dashed?: boolean;
  area?: boolean;
  hideLegend?: boolean;
}
export interface LineChartProps {
  series: Series[];
  height?: number;
  yFmt?: (v: number) => string;
  zero?: boolean;
  invert?: boolean;
  markers?: { iso: string; label: string }[];
  note?: string;
  spark?: boolean;
}

const has = (v: number | null | undefined): v is number => v !== null && v !== undefined;

export function LineChart({ series, height = 180, yFmt = (v) => fr1(v), zero = false, invert = false, markers = [], note, spark = false }: LineChartProps): ReactElement {
  const svgRef = useRef<SVGSVGElement>(null);
  const [cursor, setCursor] = useState<{ x: number; tip: string } | null>(null);
  const W = 640, H = height, L = spark ? 4 : 52, R = spark ? 4 : 10, T = spark ? 6 : 10, B = spark ? 6 : 24;
  const all = series.flatMap((s) => s.points.filter((p) => has(p[1])));
  if (all.length < 2) return <div className="empty">Pas encore assez de données.</div>;
  const xs = all.map((p) => tms(p[0])), ys = all.map((p) => p[1] as number);
  const x0 = Math.min(...xs), x1 = Math.max(...xs, ...markers.map((m) => tms(m.iso)));
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  const mid = (y0 + y1) / 2, minSpan = Math.abs(mid) * 0.04 || 1;      // courbe plate : on garde une échelle lisible
  if (y1 - y0 < minSpan) { y0 = mid - minSpan / 2; y1 = mid + minSpan / 2; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = (t: number) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (v: number) => invert ? T + ((v - y0) / (y1 - y0)) * (H - T - B) : H - B - ((v - y0) / (y1 - y0)) * (H - T - B);

  const kids: ReactNode[] = [];
  let k = 0;
  const seen = new Set<string>();
  for (const v of spark ? [] : niceTicks(y0, y1)) {
    const lbl = yFmt(v); if (seen.has(lbl)) continue; seen.add(lbl);
    kids.push(<line key={k++} x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} className={v === 0 && zero ? "axis0" : "grid"} />);
    kids.push(<text key={k++} x={L - 6} y={Y(v) + 4} textAnchor="end" className="tick">{lbl}</text>);
  }
  const span = (x1 - x0) / 864e5;
  const d = new Date(x0); d.setDate(1); d.setHours(12);
  const stepM = span > 800 ? 12 : span > 400 ? 3 : span > 120 ? 1 : 0;
  if (stepM === 0) d.setTime(x0);
  while (!spark && d.getTime() <= x1) {
    if (d.getTime() >= x0) {
      const lbl = stepM >= 12 ? String(d.getFullYear()) : stepM ? MOIS[d.getMonth()] : `${d.getDate()} ${MOIS[d.getMonth()]}`;
      kids.push(<text key={k++} x={X(d.getTime())} y={H - 6} textAnchor="middle" className="tick">{lbl}</text>);
    }
    if (stepM === 0) d.setDate(d.getDate() + 14); else d.setMonth(d.getMonth() + stepM);
  }
  for (const m of markers) {
    const x = X(tms(m.iso));
    kids.push(<line key={k++} x1={x} x2={x} y1={T} y2={H - B} className="marker" />);
    kids.push(<text key={k++} x={x - 4} y={T + 10} textAnchor="end" className="tick mk">{m.label}</text>);
  }
  for (const s of series) {
    let dpath = "", pen = false;
    for (const [iso, v] of s.points) {
      if (!has(v)) { pen = false; continue; }
      dpath += `${pen ? "L" : "M"}${X(tms(iso)).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
    }
    if (s.area && s.points.length) {
      const p0 = s.points.find((p) => p[1] !== null), pl = [...s.points].reverse().find((p) => p[1] !== null);
      if (p0 && pl) kids.push(<path key={k++} d={`${dpath}L${X(tms(pl[0]))},${Y(y0 + pad)}L${X(tms(p0[0]))},${Y(y0 + pad)}Z`} className={`area c-${s.color}`} />);
    }
    kids.push(<path key={k++} d={dpath} className={`ln c-${s.color}` + (s.dashed ? " dashed" : "")} />);
  }
  // lecture au doigt : ligne verticale + valeurs du point le plus proche
  const uniq = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))].sort();
  const show = (ev: PointerEvent<SVGSVGElement>) => {
    const g = svgRef.current; if (!g) return;
    const r = g.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
    const t = x0 + ((px - L) / (W - L - R)) * (x1 - x0);
    let best = uniq[0], bd = Infinity;
    for (const iso of uniq) { const dd = Math.abs(tms(iso) - t); if (dd < bd) { bd = dd; best = iso; } }
    const vals = series.map((s) => { const p = s.points.find((q) => q[0] === best); return p && p[1] !== null ? `${s.label} ${yFmt(p[1] as number)}` : null; })
      .filter(Boolean);
    setCursor({ x: X(tms(best)), tip: `${dayLabel(best)} · ${vals.join(" · ")}` });
  };
  const legend = series.length > 1 ? (
    <div className="legend">
      {series.filter((s) => !s.hideLegend).map((s, i) => (
        <span key={i}><i className={`c-${s.color}` + (s.dashed ? " dashed" : "")} />{s.label}</span>
      ))}
    </div>
  ) : null;
  return (
    <div className="chart">
      {legend}
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="chart-svg" role="img"
        onPointerMove={show} onPointerDown={show} onPointerLeave={() => setCursor(null)}>
        {kids}
        <line y1={T} y2={H - B} x1={cursor?.x} x2={cursor?.x} className="cursor" visibility={cursor ? "visible" : "hidden"} />
      </svg>
      <div className="chart-tip" hidden={!cursor}>{cursor?.tip}</div>
      {note ? <div className="chart-note">{note}</div> : null}
    </div>
  );
}
