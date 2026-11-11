/* v0.36 : avatar du coach, dessiné en SVG façon cel-shading (aplats + une ombre franche, contour marine).
   Femme / homme, et un signe distinctif par personnalité :
   bienveillant = grand sourire et joues roses ; exigeant = chrono et sourcil levé ;
   motivateur = poing levé et bouche ouverte ; santé = cœur sur le polo et croix sur la casquette. */
import { useState, type ReactElement } from "react";
import type { CoachGender, CoachPersonality } from "../api/types";

let coachUid = 0;
const INK = "#23406b";
const SW = 2.2;

// couleur du polo et de la casquette, et fond du médaillon
const KIT: Record<CoachPersonality, { base: string; shade: string; bg: string; bg2: string }> = {
  bienveillant: { base: "#ff9f1c", shade: "#e07a00", bg: "#ffe7b8", bg2: "#ffd37a" },
  exigeant: { base: "#3d7bd9", shade: "#2a5aa8", bg: "#d9e6f8", bg2: "#b3cbef" },
  motivateur: { base: "#ef4b3c", shade: "#c22f22", bg: "#ffd9d3", bg2: "#ffb3a8" },
  sante: { base: "#20b486", shade: "#128a64", bg: "#d4f4e7", bg2: "#a6e8cd" },
};
const LOOK: Record<CoachGender, { skin: string; skinShade: string; hair: string; hairShade: string }> = {
  femme: { skin: "#f6c9a6", skinShade: "#e0a47f", hair: "#8a4320", hairShade: "#5f2b12" },
  homme: { skin: "#e3ab82", skinShade: "#c4875e", hair: "#3a2a1f", hairShade: "#21160f" },
};

/** forme cel-shadée : aplat, ombre (à l'intérieur de la forme), puis contour par-dessus */
function Cel({ d, fill, shade, shadeD, w = SW }: { d: string; fill: string; shade?: string; shadeD?: string; w?: number }) {
  return <>
    <path d={d} fill={fill} />
    {shade && shadeD ? <path d={shadeD} fill={shade} /> : null}
    <path d={d} fill="none" stroke={INK} strokeWidth={w} strokeLinejoin="round" strokeLinecap="round" />
  </>;
}

function Emblem({ p }: { p: CoachPersonality }): ReactElement {
  // médaillon de casquette : lisible même en 32 px
  return <g>
    <circle cx={60} cy={38} r={5.6} fill="#fff" stroke={INK} strokeWidth={1.5} />
    <g transform="translate(6 5.6) scale(0.9)">
    {p === "sante" ? <path d="M58.4 31.6h3.2v2.8h2.8v3.2h-2.8v2.8h-3.2v-2.8h-2.8v-3.2h2.8z" fill="#e5383b" />
      : p === "exigeant" ? <><circle cx={60} cy={36.4} r={3.4} fill="none" stroke={INK} strokeWidth={1.3} /><path d="M60 36.4V34.2M60 36.4l1.6 1" stroke={INK} strokeWidth={1.2} strokeLinecap="round" /></>
      : p === "motivateur" ? <path d="M61.4 31.4l-4.4 5.4h3l-1.4 4.6 4.6-5.8h-3z" fill="#ffc300" stroke={INK} strokeWidth={0.8} strokeLinejoin="round" />
      : <path d="M60 39.6c-3.6-2.4-4.6-4-4.6-5.3a2.3 2.3 0 0 1 4.6-.6 2.3 2.3 0 0 1 4.6.6c0 1.3-1 2.9-4.6 5.3z" fill="#ff7a59" />}
    </g>
  </g>;
}

function Face({ g, p }: { g: CoachGender; p: CoachPersonality }): ReactElement {
  const L = LOOK[g], fem = g === "femme";
  const brow = (x: number, raised: boolean, flip: boolean) => {
    const y = raised ? 49.5 : 52.2, s = flip ? -1 : 1;
    return <path d={`M${x - 4 * s} ${y + 1} Q${x} ${y - 1.6} ${x + 4 * s} ${y + (raised ? -0.6 : 0.6)}`} fill="none"
      stroke={L.hairShade} strokeWidth={fem ? 1.6 : 2.2} strokeLinecap="round" />;
  };
  const mouth = p === "bienveillant" ? <Cel d="M52 66 Q60 75 68 66 Q60 68.5 52 66Z" fill="#fff" w={1.6} />
    : p === "motivateur" ? <><Cel d="M54 65 Q60 64 66 65 Q66 74 60 74 Q54 74 54 65Z" fill="#8c1d18" w={1.6} />
      <path d="M56.5 71.6 Q60 69.6 63.5 71.6 Q60 73.6 56.5 71.6Z" fill="#ff8a80" /></>
    : p === "exigeant" ? <path d="M54 68.5 Q60 68 66 66.4" fill="none" stroke={INK} strokeWidth={1.8} strokeLinecap="round" />
    : <path d="M54 66.6 Q60 71 66 66.6" fill="none" stroke={INK} strokeWidth={1.8} strokeLinecap="round" />;
  return <g>
    {/* oreilles + visage */}
    <Cel d="M38 58 a4.6 5.4 0 1 0 0.1 0Z" fill={L.skin} />
    <Cel d="M82 58 a4.6 5.4 0 1 1 -0.1 0Z" fill={L.skin} shade={L.skinShade} shadeD="M82.2 58.8 a3 4 0 0 1 0 7Z" />
    <Cel d="M60 39 C73 39 81.5 47 81.5 60 C81.5 73.5 72 83 60 83 C48 83 38.5 73.5 38.5 60 C38.5 47 47 39 60 39Z" fill={L.skin}
      shade={L.skinShade} shadeD="M70 42 C77.5 45.5 81.5 52 81.5 60 C81.5 73.5 72 83 60 83 C67 78 71 70 71 60 C71 52 70.5 46 70 42Z" />
    {fem ? <path d="M40 50 Q38 66 43 78 L44.6 58 Q45 52 48 48Z M80 50 Q82 66 77 78 L75.4 58 Q75 52 72 48Z" fill={L.hair} stroke={INK} strokeWidth={1.6} strokeLinejoin="round" />
      : <path d="M40 49 L40.6 60 L43.4 58 L44 49Z M80 49 L79.4 60 L76.6 58 L76 49Z" fill={L.hair} stroke={INK} strokeWidth={1.4} strokeLinejoin="round" />}
    {/* yeux, sourcils (l'exigeant lève le sien), joues, bouche */}
    {brow(52.5, false, false)}
    {brow(67.5, p === "exigeant", true)}
    <ellipse cx={52.5} cy={58} rx={2.5} ry={p === "exigeant" ? 2.4 : 3.1} fill={INK} />
    <ellipse cx={67.5} cy={58} rx={2.5} ry={3.1} fill={INK} />
    <circle cx={53.4} cy={56.9} r={0.9} fill="#fff" /><circle cx={68.4} cy={56.9} r={0.9} fill="#fff" />
    {fem ? <path d="M48.6 55.4 l-1.6 -1.2 M71.4 55.4 l1.6 -1.2" stroke={INK} strokeWidth={1.2} strokeLinecap="round" /> : null}
    {p === "bienveillant" || p === "sante" || fem ? <>
      <ellipse cx={47.5} cy={65} rx={3.2} ry={1.9} fill="#ff8f8f" opacity={0.55} />
      <ellipse cx={72.5} cy={65} rx={3.2} ry={1.9} fill="#ff8f8f" opacity={0.55} /></> : null}
    <path d="M59.6 61 Q58.4 63.6 60.6 63.8" fill="none" stroke={L.skinShade} strokeWidth={1.4} strokeLinecap="round" />
    {mouth}
    {!fem && p === "exigeant" ? <path d="M48 76 Q60 84 72 76" fill="none" stroke={L.skinShade} strokeWidth={1.2} strokeDasharray="0.1 2.2" strokeLinecap="round" /> : null}
  </g>;
}

function Ponytail({ g }: { g: CoachGender }): ReactElement | null {
  const L = LOOK[g];
  return g === "femme" ? <Cel d="M76 42 Q97 40 99 60 Q100 72 92 79 Q93 66 86 58 Q82 52 76 50Z" fill={L.hair} shade={L.hairShade}
    shadeD="M92 79 Q93 66 86 58 Q90 60 94 66 Q95 72 92 79Z" /> : null;
}

function Cap({ p }: { p: CoachPersonality }): ReactElement {
  const K = KIT[p];
  return <g>
    {/* casquette souple : calotte basse, visière courte */}
    <Cel d="M38.5 47.5 C38 36 47.5 29.5 60 29.5 C72.5 29.5 82 36 81.5 47.5 Q60 42.5 38.5 47.5Z" fill={K.base} shade={K.shade}
      shadeD="M69 31 C77 34 82 40 81.5 47.5 Q77.4 46.2 73 45.6 C73.6 39.6 72 34.4 69 31Z" />
    <path d="M60 29.6 V43.4" stroke={K.shade} strokeWidth={1.1} />
    <circle cx={60} cy={29.8} r={1.6} fill={K.shade} stroke={INK} strokeWidth={1} />
    <Cel d="M40 48.4 Q60 43 80 48.4 Q85 50.6 82 52.4 Q60 48.4 38 52.4 Q35 50.6 40 48.4Z" fill={K.shade} w={1.8} />
    <Emblem p={p} />
  </g>;
}

function Body({ g, p }: { g: CoachGender; p: CoachPersonality }): ReactElement {
  const K = KIT[p], L = LOOK[g];
  return <g>
    <Cel d="M52 78 H68 V94 H52Z" fill={L.skin} shade={L.skinShade} shadeD="M52 78 H68 V85 Q60 88 52 85Z" />
    <Cel d={g === "femme" ? "M24 141 Q24 106 42 98 L53 92 L60 101 L67 92 L78 98 Q96 106 96 141Z"
      : "M20 141 Q20 104 40 97 L53 91 L60 101 L67 91 L80 97 Q100 104 100 141Z"} fill={K.base} shade={K.shade}
      shadeD={g === "femme" ? "M78 98 Q96 106 96 141 L84 141 Q86 114 76 99Z" : "M80 97 Q100 104 100 141 L87 141 Q89 112 78 98Z"} />
    {/* col du polo, patte boutonnée */}
    <Cel d="M53 91.5 L60 101 L50.5 105 L46.5 94.6Z" fill="#fff" w={1.8} />
    <Cel d="M67 91.5 L60 101 L69.5 105 L73.5 94.6Z" fill="#fff" w={1.8} />
    <path d="M60 101 V118" stroke={INK} strokeWidth={1.6} strokeLinecap="round" />
    <circle cx={60} cy={107} r={1.1} fill={INK} /><circle cx={60} cy={113} r={1.1} fill={INK} />
    {/* cordon et sifflet */}
    <path d="M49 95 Q50 114 55 122 M71 95 Q70 112 62 121" fill="none" stroke="#ffd43b" strokeWidth={2} strokeLinecap="round" />
    <Cel d="M52 120 h10 a4.6 4.6 0 0 1 0 9.2 h-10 a2 2 0 0 1 -2 -2 v-5.2 a2 2 0 0 1 2 -2Z" fill="#e8eef6" shade="#b8c6d8"
      shadeD="M52 125.6 h10 a4.6 4.6 0 0 0 4.4 -2.2 a4.6 4.6 0 0 1 -4.4 5.8 h-10 a2 2 0 0 1 -2 -2 v-1.6Z" w={1.8} />
    <circle cx={62} cy={124.6} r={1.5} fill={INK} />
    {p === "sante" ? <Cel d="M80 121 c-6.4-4.2-8.2-7.2-8.2-9.6 a4.1 4.1 0 0 1 8.2 -1 a4.1 4.1 0 0 1 8.2 1 c0 2.4-1.8 5.4-8.2 9.6Z"
      fill="#ff5d73" shade="#d63a52" shadeD="M84.2 110.4 a4.1 4.1 0 0 1 4 1 c0 2.4-1.8 5.4-8.2 9.6 c3.6-3.4 5-6.4 4.2-10.6Z" w={1.8} /> : null}
  </g>;
}

function Prop({ g, p }: { g: CoachGender; p: CoachPersonality }): ReactElement | null {
  const K = KIT[p], L = LOOK[g];
  if (p === "motivateur") return <g>
    {/* bras levé, poing serré, traits de mouvement */}
    <Cel d="M84 104 L95 99 L106 76 L95 73Z" fill={K.base} shade={K.shade} shadeD="M100 75 L106 76 L95 99 L90 101Z" />
    <Cel d="M95.6 74 L105.4 76.6 L107 62 L97.6 61Z" fill={L.skin} />
    <Cel d="M94 50.5 h13 a3.6 3.6 0 0 1 3.6 3.6 v6.4 a3.6 3.6 0 0 1 -3.6 3.6 h-11.4 a3.6 3.6 0 0 1 -3.6 -3.6 v-8 a2 2 0 0 1 2 -2Z"
      fill={L.skin} shade={L.skinShade} shadeD="M106 50.6 h1 a3.6 3.6 0 0 1 3.6 3.6 v6.4 a3.6 3.6 0 0 1 -3.6 3.6 h-1Z" />
    <path d="M96.2 55 h11 M96.2 59.2 h11" stroke={INK} strokeWidth={1.2} strokeLinecap="round" />
    <path d="M112 44 l5 -4 M114.6 52 l5.4 -1 M88 44 l-3.6 -4.4" stroke={INK} strokeWidth={2} strokeLinecap="round" />
  </g>;
  if (p === "exigeant") return <g>
    {/* chrono tenu dans la main */}
    <Cel d="M97 92 h6 v4 h-6Z" fill="#c9d3e0" w={1.6} />
    <Cel d="M100 95.5 a12 12 0 1 1 -0.1 0Z" fill="#f4f7fb" shade="#c9d3e0" shadeD="M106 97.2 a12 12 0 0 1 -6 22.3 a12 12 0 0 0 6 -22.3Z" />
    <circle cx={100} cy={107.5} r={8.2} fill="none" stroke={INK} strokeWidth={1.2} />
    <path d="M100 107.5 V101.6 M100 107.5 l4 2.2" stroke="#e5383b" strokeWidth={1.8} strokeLinecap="round" />
    <Cel d="M89 116 q-3 6 2 10 q8 4 15 -2 q2 -3 -1 -5Z" fill={L.skin} shade={L.skinShade} shadeD="M104 119 q2 2 1 4 q-5 5 -12 3 q8 -2 11 -7Z" />
  </g>;
  if (p === "bienveillant") return <g>
    {/* étincelles chaleureuses */}
    <path d="M100 34 l1.6 4 4 1.6 -4 1.6 -1.6 4 -1.6 -4 -4 -1.6 4 -1.6Z" fill="#ffd43b" stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
    <path d="M20 54 l1.1 2.8 2.8 1.1 -2.8 1.1 -1.1 2.8 -1.1 -2.8 -2.8 -1.1 2.8 -1.1Z" fill="#ffd43b" stroke={INK} strokeWidth={1} strokeLinejoin="round" />
  </g>;
  return null;
}

export interface CoachAvatarProps {
  gender: CoachGender;
  personality: CoachPersonality;
  /** taille en px ; head = médaillon rond (bulles du chat), sinon buste */
  size?: number;
  head?: boolean;
  title?: string;
  className?: string;
}

export function CoachAvatar({ gender, personality, size = 120, head = false, title, className }: CoachAvatarProps): ReactElement {
  const [id] = useState(() => `cav${++coachUid}`);
  const K = KIT[personality] || KIT.bienveillant;
  const g: CoachGender = gender === "homme" ? "homme" : "femme";
  const p: CoachPersonality = KIT[personality] ? personality : "bienveillant";
  const vb = head ? "26 20 68 68" : "0 0 120 142";
  return (
    <svg viewBox={vb} width={size} height={head ? size : Math.round(size * 142 / 120)} className={"coach-av" + (className ? " " + className : "")}
      role={title ? "img" : undefined} aria-hidden={title ? undefined : true} aria-label={title}>
      <defs>
        <clipPath id={`${id}c`}><circle cx={60} cy={54} r={33} /></clipPath>
      </defs>
      {head ? <>
        <circle cx={60} cy={54} r={33} fill={K.bg} />
        <g clipPath={`url(#${id}c)`}>
          <path d="M27 70 Q60 60 93 70 V90 H27Z" fill={K.bg2} />
          <Ponytail g={g} />
          <Body g={g} p={p} />
          <Face g={g} p={p} />
          <Cap p={p} />
        </g>
        <circle cx={60} cy={54} r={32.4} fill="none" stroke={INK} strokeWidth={2.4} />
      </> : <>
        <rect x={4} y={4} width={112} height={134} rx={22} fill={K.bg} />
        <path d="M4 100 Q60 84 116 100 V116 a22 22 0 0 1 -22 22 H26 a22 22 0 0 1 -22 -22Z" fill={K.bg2} />
        <Ponytail g={g} />
        <Body g={g} p={p} />
        <Face g={g} p={p} />
        <Cap p={p} />
        <Prop g={g} p={p} />
      </>}
    </svg>
  );
}
