/* profil du coureur en radar (v0.20) */
import type { ReactElement } from "react";
import type { ProfileAxis } from "../api/types";
import { fr1 } from "../lib/format";

export function RadarChart({ axes }: { axes: ProfileAxis[] }): ReactElement {
  const S = 340, c = S / 2, R = 112, n = axes.length;
  const ang = (i: number) => -Math.PI / 2 + (2 * Math.PI * i) / n;
  const pt = (i: number, v: number): [number, number] => [c + Math.cos(ang(i)) * R * v / 10, c + Math.sin(ang(i)) * R * v / 10];
  const poly = (key: "score" | "score_3m", cls: string) => {
    const pts = axes.map((a, i) => (a[key] === null ? null : pt(i, a[key] as number))).filter((p): p is [number, number] => p !== null);
    return pts.length >= 3 ? <polygon points={pts.map((p) => p.join(",")).join(" ")} className={cls} /> : null;
  };
  return (
    <svg viewBox={`-34 0 ${S + 68} ${S}`} className="radar-svg" role="img" aria-label="Profil du coureur">
      {[2, 4, 6, 8, 10].map((lv) => (
        <polygon key={lv} points={axes.map((_, i) => pt(i, lv).join(",")).join(" ")} className={lv === 10 ? "ring outer" : "ring"} />
      ))}
      {axes.map((a, i) => {
        const [x, y] = pt(i, 10);
        const [lx, ly] = pt(i, 12.6), anchor = Math.abs(lx - c) < 8 ? "middle" : lx > c ? "start" : "end";
        const words = a.label.split(" ");
        const lines = words.length > 2 ? [words.slice(0, 2).join(" "), words.slice(2).join(" ")]
          : words.length === 2 && a.label.length > 14 ? words : [a.label];
        return [
          <line key={`s${i}`} x1={c} y1={c} x2={x} y2={y} className="spoke" />,
          <text key={`t${i}`} x={lx} y={ly - (lines.length - 1) * 6} textAnchor={anchor} className={"rlabel" + (a.score === null ? " off" : "")}>
            {lines.map((l, kk) => <tspan key={kk} x={lx} dy={kk ? 12 : 0}>{l}</tspan>)}
            <tspan x={lx} dy={13} className="rscore">{a.score === null ? "?" : fr1(a.score)}</tspan>
          </text>,
        ];
      })}
      {poly("score_3m", "rp old")}
      {poly("score", "rp now")}
      {axes.map((a, i) => {
        if (a.score === null) return null;
        const [x, y] = pt(i, a.score);
        return <circle key={`d${i}`} cx={x} cy={y} r={3.5} className="rdot" />;
      })}
    </svg>
  );
}
