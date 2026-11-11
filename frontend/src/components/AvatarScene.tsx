/* v0.47 : décor de l'avatar = un lieu (paysage) × une ambiance (saison, neige, nuit), en aplats façon cel-shading.
 * L'ambiance passe par la palette (lib/scene.ts) et ajoute ses effets : flocons, étoiles et lune, feuilles, fleurs, soleil. */
import { cloneElement, type ReactElement, type ReactNode } from "react";
import { mix, palette, type Ambiance, type Palette, type Place } from "../lib/scene";

type Pt = [number, number];

/** Calotte de neige d'un pic : du sommet jusqu'à la fraction `f` de chaque pente, bord en dents de scie. */
function capPath(top: Pt, left: Pt, right: Pt, f: number): string {
  const at = (q: Pt, t: number): Pt => [top[0] + (q[0] - top[0]) * t, top[1] + (q[1] - top[1]) * t];
  const l = at(left, f), r = at(right, f), m1 = at(left, f * 0.55), m2 = at(right, f * 0.55);
  const mid: Pt = [(l[0] + r[0]) / 2, (l[1] + r[1]) / 2 - 2];
  const pts: Pt[] = [top, l, [(l[0] + m1[0]) / 2 + 2, (l[1] + mid[1]) / 2 + 1], mid, [(r[0] + m2[0]) / 2 - 2, (r[1] + mid[1]) / 2 + 1], r];
  return "M" + pts.map((q) => q.map((v) => v.toFixed(1)).join(" ")).join(" L") + "Z";
}

const FLAKES: Pt[] = [[8, 14], [26, 40], [46, 10], [70, 18], [100, 12], [114, 44], [18, 70], [40, 58], [92, 62], [108, 84],
  [6, 98], [34, 86], [84, 40], [56, 4], [28, 112], [98, 104], [12, 126], [112, 120], [48, 30], [76, 92], [64, 116], [88, 128]];
const STARS: Pt[] = [[8, 10], [40, 8], [52, 18], [84, 10], [100, 26], [112, 8], [116, 54], [6, 46], [36, 34], [92, 46], [74, 4]];

export function sceneLayers(place: Place, ambiance: Ambiance, uid: string): ReactNode[] {
  const P: Palette = palette(place, ambiance);
  const out: ReactNode[] = [];
  let k = 0;
  const add = (n: ReactElement) => out.push(cloneElement(n, { key: `s${k++}` }));
  const night = ambiance === "nuit";

  // ciel en dégradé
  add(<defs><linearGradient id={`${uid}sky`} x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stopColor={P.sky0} /><stop offset="1" stopColor={P.sky1} />
  </linearGradient></defs>);
  add(<rect x={0} y={0} width={120} height={150} fill={`url(#${uid}sky)`} />);

  // astres
  if (P.sun === "moon") {
    for (const [x, y] of STARS) add(<circle cx={x} cy={y} r={0.9 + ((x + y) % 3) * 0.25} fill="#fef9c3" />);
    add(<circle cx={22} cy={24} r={11} fill="#fef3c7" opacity=".18" />);
    add(<circle cx={22} cy={24} r={7} fill="#fef3c7" />);
    add(<circle cx={24.5} cy={22} r={1.4} fill="#e7d9a8" />);
    add(<circle cx={19.5} cy={26.5} r={1} fill="#e7d9a8" />);
  } else if (P.sun === "big") {
    add(<circle cx={96} cy={28} r={16} fill="#fde047" opacity=".3" />);
    add(<circle cx={96} cy={28} r={11} fill="#facc15" />);
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4;
      add(<path d={`M${(96 + Math.cos(a) * 15).toFixed(1)} ${(28 + Math.sin(a) * 15).toFixed(1)} L${(96 + Math.cos(a) * 20).toFixed(1)} ${(28 + Math.sin(a) * 20).toFixed(1)}`}
        stroke="#facc15" strokeWidth={2} strokeLinecap="round" />);
    }
  } else if (P.sun === "sun") add(<circle cx={98} cy={30} r={9} fill="#fde047" />);
  else if (P.sun === "pale") add(<circle cx={98} cy={30} r={9} fill="#f8fafc" opacity=".85" />);
  else if (P.sun === "sunrise") {
    add(<circle cx={88} cy={92} r={26} fill="#fff7ed" opacity=".55" />);
    add(<circle cx={88} cy={92} r={15} fill="#fde68a" />);
  }
  // nuages (printemps, automne, hiver, neige : ciel couvert)
  const cloud = (x: number, y: number, s: number) => {
    add(<ellipse cx={x} cy={y} rx={9 * s} ry={3.6 * s} fill={P.cloud} opacity=".9" />);
    add(<ellipse cx={x - 3 * s} cy={y - 2.4 * s} rx={4.6 * s} ry={3.4 * s} fill={P.cloud} opacity=".9" />);
    add(<ellipse cx={x + 3 * s} cy={y - 2 * s} rx={3.8 * s} ry={3 * s} fill={P.cloud} opacity=".9" />);
  };
  if (ambiance === "printemps" || ambiance === "automne") { cloud(30, 30, 1); cloud(104, 56, 0.7); }
  if (ambiance === "neige" || ambiance === "hiver") { cloud(26, 22, 1.2); cloud(92, 16, 1.4); cloud(112, 40, 0.8); }

  // arbres
  const pine = (x: number, base: number, h: number, light = P.pine, dark = P.pineShade) => {
    add(<rect x={x - 1.5} y={base - 6} width={3} height={6} fill={P.trunk} />);
    add(<path d={`M${x} ${base - h} l-11 ${h - 5} h11z`} fill={light} />);
    add(<path d={`M${x} ${base - h} l11 ${h - 5} h-11z`} fill={dark} />);
    if (P.trees === "snowy" || P.trees === "bare") {
      const f = P.trees === "snowy" ? 0.45 : 0.2;
      add(<path d={capPath([x, base - h], [x - 11, base - 5], [x + 11, base - 5], f)} fill={P.cap} />);
      if (P.trees === "snowy") add(<path d={`M${x - 8} ${base - h * 0.42} q8 -4 16 0 l2 3 q-10 -2 -20 0z`} fill={P.cap} />);
    }
  };
  const tree = (x: number, y: number, r: number, alt = false) => {
    add(<rect x={x - 1.6} y={y} width={3.2} height={136 - y} fill={P.trunk} />);
    if (P.trees === "bare" || P.trees === "snowy") {
      add(<path d={`M${x} ${y + 4} l-${r * 0.7} -${r * 0.8} M${x} ${y + 1} l${r * 0.6} -${r} M${x} ${y + 8} l${r * 0.8} -${r * 0.4} M${x - r * 0.35} ${y - r * 0.4} l-2 -4`}
        stroke={P.trunk} strokeWidth={1.6} strokeLinecap="round" />);
      if (P.trees === "snowy") {
        add(<path d={`M${x - r * 0.9} ${y - r * 0.85} l3 -1.5 l2 1.8z M${x + r * 0.45} ${y - r * 1.05} l3 -1.5 l2 1.8z M${x + r * 0.65} ${y + 3.2} l3 -1.4 l2 1.6z`} fill={P.cap} />);
        add(<ellipse cx={x} cy={136} rx={r * 0.9} ry={2.4} fill={P.cap} />);
      }
      return;
    }
    const crown = alt ? P.foliageAlt : P.foliage;
    add(<circle cx={x} cy={y} r={r} fill={P.foliageShade} />);
    add(<circle cx={x - r * 0.18} cy={y - r * 0.15} r={r * 0.82} fill={crown} />);
    if (P.trees === "blossom") for (const [dx, dy] of [[-0.4, -0.3], [0.3, -0.45], [0.45, 0.15], [-0.15, 0.35], [0, -0.05]] as Pt[])
      add(<circle cx={x + dx * r} cy={y + dy * r} r={r * 0.2} fill={dx > 0 ? "#fbcfe8" : "#f9a8d4"} />);
  };
  const peakShade = (top: Pt, right: Pt, bottomY: number) =>
    add(<path d={`M${top[0]} ${top[1]} L${right[0]} ${right[1]} L${right[0]} ${bottomY} L${top[0] + (right[0] - top[0]) * 0.25} ${bottomY}Z`} fill={P.rockShade} />);

  // ---- le lieu
  if (place === "route") {
    add(<path d="M0 112 Q30 94 60 106 T120 98 V136 H0Z" fill={P.far} />);
    add(<path d="M0 124 Q60 112 120 122 V136 H0Z" fill={P.grass} />);
    add(<path d="M0 130 Q60 122 120 130 V136 H0Z" fill={P.grassShade} />);
    tree(14, 102, 11); tree(106, 98, 12, true);
    // réverbère (allumé la nuit)
    add(<rect x={88} y={92} width={1.6} height={44} fill={P.buildingShade} />);
    add(<rect x={85.5} y={89} width={6.5} height={3.5} rx={1} fill={P.buildingShade} />);
    if (night) add(<circle cx={88.8} cy={93} r={7} fill="#fde047" opacity=".35" />);
  }
  if (place === "collines") {
    add(<path d="M0 102 Q25 78 55 96 T120 86 V136 H0Z" fill={P.far} />);
    add(<path d="M55 96 Q80 106 120 86 V100 Q90 108 60 100Z" fill={P.farShade} />);
    add(<path d="M0 118 Q35 96 70 112 T120 106 V136 H0Z" fill={P.grass} />);
    add(<path d="M70 112 Q95 124 120 106 V136 H84Z" fill={P.grassShade} />);
    add(<path d="M14 136 Q30 120 50 118 T80 108" fill="none" stroke={P.ground} strokeWidth={3} strokeLinecap="round" opacity=".8" />);
    tree(104, 96, 8, true);
  }
  if (place === "foret") {
    for (const [x, h] of [[40, 40], [58, 34], [80, 44]] as Pt[]) pine(x, 124, h, P.far, P.farShade);
    add(<path d="M0 124 Q60 116 120 124 V136 H0Z" fill={P.grass} />);
    pine(10, 138, 72); tree(32, 98, 12, true); pine(98, 138, 64); pine(115, 138, 78);
  }
  if (place === "bord_de_mer") {
    add(<rect x={0} y={90} width={120} height={30} fill={P.sea} />);
    add(<rect x={0} y={90} width={120} height={3} fill={P.seaDeep} />);
    for (const y of [99, 107]) add(<path d={`M${y % 2 ? 4 : 10} ${y} q5 -2.5 10 0 m14 0 q5 -2.5 10 0 m18 0 q5 -2.5 10 0 m14 0 q5 -2.5 10 0`}
      fill="none" stroke={P.foam} strokeWidth={1.1} opacity=".75" />);
    if (night) for (let i = 0; i < 5; i++) add(<rect x={18 - i * 0.8} y={93 + i * 4.5} width={8 + i * 1.6} height={1.4} rx={0.7} fill="#fef3c7" opacity={0.8 - i * 0.12} />);
    else if (P.sun === "sun" || P.sun === "big") for (let i = 0; i < 3; i++) add(<rect x={92 - i} y={94 + i * 5} width={10 + i * 2} height={1.3} rx={0.6} fill="#fef9c3" opacity=".7" />);
    add(<path d="M0 116 Q40 108 80 114 T120 110 V136 H0Z" fill={P.sand} />);
    add(<path d="M80 114 Q100 120 120 110 V136 H94Z" fill={P.sandShade} />);
    add(<path d="M2 118 q14 -4 26 -1" fill="none" stroke={P.foam} strokeWidth={1.4} opacity=".8" />);
    // rocher de la plage
    add(<path d="M100 122 q4 -8 10 -6 q6 0 7 8z" fill={mix(P.sandShade, "#57534e", 0.55)} />);
    if (P.trees === "snowy") add(<path d="M101 119 q4 -5 9 -4 q5 0 6 5 q-8 -2 -15 -1z" fill={P.cap} />);
  }
  if (place === "montagne") {
    add(<path d="M-5 106 L18 80 L34 96 L58 70 L84 98 L104 78 L125 92 V136 H-5Z" fill={P.far} />);
    if (P.caps > 0) for (const [t, l, r] of [[[58, 70], [40, 92], [80, 95]], [[104, 78], [92, 90], [118, 88]], [[18, 80], [4, 96], [32, 94]]] as [Pt, Pt, Pt][])
      add(<path d={capPath(t, l, r, Math.min(0.7, P.caps * 1.3))} fill={P.cap} opacity=".85" />);
    add(<path d="M-5 124 L30 62 L52 96 L78 50 L125 122 V136 H-5Z" fill={P.rock} />);
    peakShade([78, 50], [125, 122], 136); peakShade([30, 62], [52, 96], 112);
    if (P.caps > 0) {
      add(<path d={capPath([78, 50], [52, 96], [125, 122], P.caps)} fill={P.cap} />);
      add(<path d={capPath([30, 62], [-5, 124], [52, 96], P.caps)} fill={P.cap} />);
    }
    add(<path d="M0 128 Q60 120 120 128 V136 H0Z" fill={P.grass} />);
  }
  if (place === "sommet") {
    for (const [x, y, w] of [[10, 104, 14], [48, 100, 18], [110, 102, 12]]) {
      add(<path d={`M${x - w} ${y + 14} L${x} ${y} L${x + w} ${y + 14}Z`} fill={P.far} />);
      if (P.caps > 0) add(<path d={capPath([x, y], [x - w, y + 14], [x + w, y + 14], Math.min(0.7, P.caps * 1.6))} fill={P.cap} opacity=".9" />);
    }
    for (const [x, y, s] of [[20, 116, 1.4], [60, 114, 1.6], [100, 117, 1.3]]) cloud(x, y, s);
    add(<rect x={0} y={115} width={120} height={8} fill={P.cloud} opacity=".85" />);
    add(<path d="M-5 136 L-5 116 L36 108 L66 100 L100 74 L125 86 V136Z" fill={P.rock} />);
    peakShade([100, 74], [125, 86], 136);
    if (P.caps > 0) add(<path d={capPath([100, 74], [66, 100], [125, 86], Math.min(0.8, P.caps * 1.4))} fill={P.cap} />);
    add(<rect x={99.4} y={58} width={1.4} height={17} fill="#44403c" />);
    add(<path d="M100.8 58.5 L110 61.5 L100.8 64.5Z" fill="#dc2626" />);
  }
  if (place === "ville") {
    for (const [x, w2, hgt] of [[0, 18, 72], [20, 14, 52], [86, 16, 64], [104, 16, 86]]) {
      const top = 136 - hgt;
      add(<rect x={x} y={top} width={w2} height={hgt} fill={P.building} />);
      add(<rect x={x + w2 - 4} y={top} width={4} height={hgt} fill={P.buildingShade} />);
      add(<rect x={x - 1} y={top - 2} width={w2 + 2} height={2.5} fill={P.roof} />);
      let n = 0;
      for (let yy = top + 6; yy < 128; yy += 10) for (const dx of w2 > 15 ? [3, 9] : [4]) {
        const lit = !night || (n++ * 7 + x) % 5 !== 0;
        add(<rect x={x + dx} y={yy} width={4} height={4} fill={lit ? P.window : P.buildingShade} />);
        if (P.trees === "snowy") add(<rect x={x + dx - 0.5} y={yy + 4} width={5} height={1} fill={P.cap} />);
      }
    }
    add(<rect x={0} y={132} width={120} height={4} fill={P.groundEdge} />);
  }
  if (place === "volcan") {
    add(<path d="M-5 120 Q20 112 40 116 L40 136 H-5Z" fill={P.far} />);
    add(<path d="M-5 136 L40 60 L54 60 L70 66 L84 60 L125 136Z" fill={P.rock} />);
    add(<path d="M84 60 L125 136 L96 136Z" fill={P.rockShade} />);
    if (P.caps > 0) {                                        // neige sur les flancs, le cratère reste sombre
      const f = Math.min(0.42, P.caps * 0.6), ly = 60 + 76 * f, lx = 40 - 45 * f, rx = 84 + 41 * f;
      const j = (x: number, y: number) => `${x.toFixed(1)} ${y.toFixed(1)}`;
      add(<path d={`M${j(lx, ly)} L40 60 L54 60 L70 66 L84 60 L${j(rx, ly)} L${j(rx - 9, ly - 5)} L${j(rx - 16, ly + 2)} L${j(62, ly - 6)} L${j(50, ly + 2)} L${j(lx + 14, ly - 5)} L${j(lx + 7, ly + 1)}Z`} fill={P.cap} />);
      add(<path d="M42 61 L54 61 L70 67 L82 61 L80 64 L70 70 L54 64.5 L44 64Z" fill={P.rockShade} />);
    }
    if (night) add(<ellipse cx={62} cy={62} rx={20} ry={9} fill="#f97316" opacity=".45" />);
    add(<path d="M54 60 Q62 70 70 66 L64 90 Q60 100 56 112" fill="none" stroke={P.lava} strokeWidth={2.5} />);
    add(<path d="M50 52 q4 -12 12 -8 q6 -10 14 -2" fill="none" stroke={P.smoke} strokeWidth={3} opacity=".7" />);
  }

  // ---- effets de l'ambiance
  if (ambiance === "printemps") for (const [x, y, c] of [[6, 132, "#f9a8d4"], [14, 128, "#fde047"], [24, 133, "#ffffff"], [94, 131, "#f9a8d4"],
    [104, 127, "#ffffff"], [114, 132, "#fde047"]] as [number, number, string][]) {
    add(<circle cx={x} cy={y} r={1.6} fill={c} />);
    add(<circle cx={x} cy={y} r={0.6} fill="#f59e0b" />);
  }
  if (ambiance === "automne") for (const [x, y, c] of [[28, 60, "#ea580c"], [92, 70, "#dc2626"], [44, 102, "#f59e0b"], [104, 50, "#f97316"],
    [16, 118, "#b45309"], [100, 112, "#ea580c"], [10, 142, "#f97316"], [110, 144, "#dc2626"]] as [number, number, string][])
    add(<ellipse cx={x} cy={y} rx={2.6} ry={1.3} fill={c} transform={`rotate(${(x * 7) % 180} ${x} ${y})`} />);

  // sol (le chemin)
  add(<rect x={0} y={136} width={120} height={14} fill={P.ground} />);
  add(<rect x={0} y={136} width={120} height={1.6} fill={P.groundEdge} />);
  if (ambiance === "neige") add(<path d="M0 137.6 q10 3 20 0 t20 0 t20 0 t20 0 t20 0 t20 0" fill="none" stroke={P.groundEdge} strokeWidth={0.8} />);
  if (P.flakes) for (const [x, y] of FLAKES.slice(0, P.flakes)) add(<circle cx={x} cy={y} r={ambiance === "neige" ? 1.3 + (x % 3) * 0.3 : 1.1} fill="#ffffff" opacity=".9" />);
  return out;
}

