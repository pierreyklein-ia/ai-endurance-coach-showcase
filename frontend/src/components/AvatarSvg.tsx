/* Avatar dessiné (SVG), port fidèle de avatarSvg() de l'interface historique. */
import { cloneElement, useState, type ReactElement, type ReactNode } from "react";
import type { AvatarCfg, Career, GearItem } from "../api/types";
import { AV, wornGear } from "../lib/avatar";
import { sceneParts } from "../lib/scene";
import { sceneLayers } from "./AvatarScene";

let avatarUid = 0;

const HAIR_D: Record<string, string> = {
  court: "M57 37 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 39Z",
  long: "M57 38 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 40Z",
  chignon: "M57 37 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 39Z",
  boucles: "M56 39 Q53 30 58 26 Q61 21 67 23 Q73 20 77 25 Q82 27 79 33 Q73 29 66 31 Q61 32 60 40Z",
  rase: "M57.5 36 Q58 26 69 26 Q78 26 79 33 Q72 29.5 66 30.5 Q61 31.5 60 38Z",
};

function Limb({ pts, color, w }: { pts: number[][]; color: string; w: number }) {
  return <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth={w}
    strokeLinecap="round" strokeLinejoin="round" />;
}

export interface AvatarProps {
  cfg: AvatarCfg;
  level: number;
  gear: GearItem[];
  /** ancien décor unique (avant v0.47), utilisé si lieu et ambiance ne sont pas donnés */
  scene?: string | null;
  place?: string | null;
  ambiance?: string | null;
  size?: number;
  head?: boolean;
}

export function AvatarSvg({ cfg, level, gear, scene, place, ambiance, size = 160, head = false }: AvatarProps): ReactElement {
  const [id] = useState(() => `av${++avatarUid}`);
  const skin = AV.skin[cfg.skin] || AV.skin[2], hair = AV.hairColor[cfg.hair_color] || AV.hairColor[1];
  const w = wornGear(cfg, gear);
  const shirt = AV.shirt[cfg.shirt] || AV.shirt[0], shorts = AV.shorts[cfg.shorts] || AV.shorts[1];
  const shoes = w.has("chaussures_or") ? "#eab308" : AV.shoes[cfg.shoes] || AV.shoes[2];
  const vb = head ? "44 14 48 48" : "0 0 120 150";
  const sp = sceneParts(scene, place, ambiance);
  const bg: ReactNode[] = [];
  let k = 0;
  const add = (n: ReactElement) => bg.push(cloneElement(n, { key: k++ }));
  // décor (v0.47) : lieu × ambiance
  bg.push(...sceneLayers(sp.place, sp.ambiance, id));
  add(<ellipse cx={60} cy={138} rx={26} ry={3.5} fill="#000" opacity=".12" />);
  if (level >= 5 && !head) for (const y of [70, 80, 90])
    add(<path d={`M${14 + (y % 20)} ${y} h14`} stroke="#fff" strokeWidth={2.5} strokeLinecap="round" opacity=".7" />);
  if (w.has("aura")) add(<ellipse cx={64} cy={80} rx={38} ry={56} fill="#fde047" opacity=".28" />);
  const stars = Math.max(0, level - 20);                 // v0.34 : étoiles au-delà du niveau 20
  for (let i = 0; i < Math.min(stars, 8) && !head; i++)
    add(<path d="M0 -4 L1.2 -1.2 L4 -1 L1.8 .9 L2.5 3.8 L0 2.2 L-2.5 3.8 L-1.8 .9 L-4 -1 L-1.2 -1.2Z" fill="#facc15" stroke="#a16207"
      strokeWidth={0.4} transform={`translate(${108 - (i % 4) * 10} ${12 + Math.floor(i / 4) * 10})`} />);

  const laurier: ReactNode[] = [];
  if (w.has("laurier")) for (let i = 0; i < 7; i++) {
    const a = Math.PI * (1.05 + i * 0.14);
    const cx = 68 + Math.cos(a) * 11.5, cy = 35 + Math.sin(a) * 11.5;
    laurier.push(<ellipse key={i} cx={cx} cy={cy} rx={2.6} ry={1.3} fill="#16a34a" transform={`rotate(${(a * 180) / Math.PI + 90} ${cx} ${cy})`} />);
  }

  return (
    <svg viewBox={vb} width={size} height={head ? size : size * 1.25} className="avatar-svg" role="img" aria-label={`Avatar niveau ${level}`}>
      <defs>
        <clipPath id={`${id}c`}>
          {head ? <circle cx={68} cy={38} r={24} /> : <rect x={0} y={0} width={120} height={150} rx={22} />}
        </clipPath>
      </defs>
      <g clipPath={`url(#${id}c)`}>
        {bg}
        <g>
          {/* bâtons (derrière) */}
          {w.has("camelbak") && <rect x={44} y={49} width={15} height={34} rx={5} fill="#0f766e" stroke="#134e4a" strokeWidth={0.8} />}
          {w.has("batons") && <><Limb pts={[[44, 62], [30, 137]]} color="#475569" w={2} /><Limb pts={[[86, 62], [100, 137]]} color="#475569" w={2} /></>}
          {/* queue de cheval (derrière la tête) */}
          {cfg.hair === "long" && <path d="M58 33 Q44 34 40 48 Q48 42 57 41Z" fill={hair} />}
          {/* jambe et bras arrière */}
          <Limb pts={[[60, 88], [49, 106]]} color={shorts} w={9} />
          <Limb pts={[[49, 106], [37, 120]]} color={skin} w={6.5} />
          <path d="M31 118 l9 4 l-2 5 l-11 -3z" fill={shoes} stroke="#0002" strokeWidth={0.8} />
          <Limb pts={[[62, 56], [51, 70], [44, 62]]} color={skin} w={5.5} />
          {/* torse */}
          <path d="M58 52 Q66 47 72 53 L68 90 Q61 93 55 89Z" fill={shirt} />
          {w.has("coupe_vent") && <>
            <path d="M57 52 Q66 46 73 53 L69 91 Q61 94 54 90Z" fill="#0ea5e9" opacity=".85" />
            <path d="M65 50 L62 91" stroke="#e0f2fe" strokeWidth={0.8} />
          </>}
          {w.has("gilet") && <>
            <path d="M57 54 L61 52 L59 88 L55 88Z M69 53 L72 54 L68 89 L64 89Z" fill="#111827" opacity=".85" />
            <rect x={66} y={58} width={5} height={9} rx={2} fill="#38bdf8" stroke="#0369a1" strokeWidth={0.6} />
          </>}
          {w.has("camelbak") && <>
            <path d="M57 53 L70 64 M57 74 L68 79" stroke="#134e4a" strokeWidth={1.8} strokeLinecap="round" />
            <path d="M58 52 Q66 47 70 57" fill="none" stroke="#1e293b" strokeWidth={1.2} />
            <circle cx={70} cy={57.5} r={1.4} fill="#38bdf8" />
          </>}
          {w.has("dossard") && <>
            <rect x={58} y={66} width={11} height={9} rx={1.5} fill="#fff" stroke="#94a3b8" strokeWidth={0.6} />
            <text x={63.5} y={73} textAnchor="middle" fontSize={6} fontWeight={800} fill="#0f172a">{String(level)}</text>
          </>}
          {w.has("medaille") && <>
            <path d="M62 52 L65 62 L68 52" fill="none" stroke="#dc2626" strokeWidth={1.6} />
            <circle cx={65} cy={64} r={3} fill="#eab308" stroke="#a16207" strokeWidth={0.6} />
          </>}
          {/* short, jambe avant */}
          <path d="M54 86 L69 87 L70 95 L56 96Z" fill={shorts} />
          <Limb pts={[[63, 90], [77, 104]]} color={shorts} w={9} />
          <Limb pts={[[77, 104], [73, 128]]} color={skin} w={6.5} />
          <path d="M70 126 l12 0 q3 0 3 3 l0 2 l-16 0z" fill={shoes} stroke="#0002" strokeWidth={0.8} />
          {w.has("manchons") && <><Limb pts={[[76.4, 108], [74, 122]]} color="#111827" w={6.8} /><Limb pts={[[46.5, 109.5], [40.5, 117]]} color="#111827" w={6.8} /></>}
          {w.has("guetres") && <>
            <rect x={69.5} y={121.5} width={10} height={5.5} rx={1.5} fill="#64748b" />
            <rect x={31} y={114} width={9} height={5.5} rx={1.5} fill="#64748b" transform="rotate(20 35 117)" />
          </>}
          {/* cou, tête */}
          <Limb pts={[[66, 46], [66, 52]]} color={skin} w={6} />
          <circle cx={68} cy={38} r={11} fill={skin} />
          <circle cx={73.5} cy={36.5} r={1.3} fill="#1f2937" />
          <path d="M72 43 q2.5 1.2 4.5 -.6" fill="none" stroke="#7c2d12" strokeWidth={1} strokeLinecap="round" />
          <circle cx={63} cy={39} r={2.2} fill={skin} stroke="#0001" />
          {w.has("buff") && <>
            <rect x={61.5} y={46} width={10} height={5.5} rx={2} fill="#f97316" />
            <path d="M62 48.5 h9" stroke="#fde68a" strokeWidth={1} />
          </>}
          {/* cheveux */}
          {cfg.hair === "chignon" && <circle cx={58} cy={29} r={4.5} fill={hair} />}
          {cfg.hair === "boucles" && [[57, 31], [60, 25], [66, 22], [73, 22], [78, 27]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3.2} fill={hair} />)}
          <path d={HAIR_D[cfg.hair] || ""} fill={hair} opacity={cfg.hair === "rase" ? ".55" : "1"} />
          {cfg.beard && <path d="M61 41 Q63 50 70 50 Q77 49 78 42 Q74 45 70 45 Q65 45 61 41Z" fill={hair} opacity=".9" />}
          {/* équipement de tête */}
          {w.has("laurier") && <><circle cx={68} cy={37} r={15} fill="none" stroke="#facc15" strokeWidth={1.2} opacity=".7" />{laurier}</>}
          {w.has("bandeau") && <path d="M57.3 33 Q68 28.5 79.2 32.5 L79.6 35.5 Q68 31.5 57.4 36.2Z" fill="#ef4444" />}
          {w.has("casquette") && <>
            <path d="M57 33 Q57 23 69 23 Q80 24 80 32Z" fill="#f8fafc" stroke="#cbd5e1" strokeWidth={0.7} />
            <path d="M78 31 Q86 31 89 33 Q84 34.5 79 33.6Z" fill="#e2e8f0" />
          </>}
          {/* v0.33 : chapeaux de saison */}
          {w.has("bonnet_noel") && <>
            <path d="M57 33 Q60 18 72 20 Q82 22 86 34 Q78 26 70 27 Q62 28 57 33Z" fill="#dc2626" />
            <path d="M56 33 Q68 28 80 32 L80 36 Q68 32 56 37Z" fill="#f8fafc" />
            <circle cx={87} cy={35} r={3} fill="#f8fafc" />
          </>}
          {w.has("chapeau_sorciere") && <>
            <path d="M50 33 Q68 27 86 32 L86 34.5 Q68 30.5 50 36Z" fill="#1f2937" />
            <path d="M58 32 L70 6 L74 14 L80 31Z" fill="#1f2937" />
            <path d="M58.5 30 Q69 27 79.5 29.5 L79.8 31.5 Q69 29 58.4 32Z" fill="#7c3aed" />
          </>}
          {w.has("chapeau_fete") && <>
            <path d="M61 29 L69 6 L77 28Z" fill="#ec4899" />
            <path d="M64.5 19 L73.5 19.5 M62.8 24 L75.4 24.3" stroke="#fde047" strokeWidth={1.6} />
            <circle cx={69} cy={6} r={2.4} fill="#facc15" />
          </>}
          {w.has("cotillons") && <>
            {[[16, 22, "#ec4899"], [30, 12, "#22c55e"], [98, 16, "#3b82f6"], [108, 30, "#f59e0b"], [20, 48, "#a855f7"], [102, 52, "#ef4444"], [44, 8, "#facc15"]].map(([x, y, col], i) => (
              <rect key={i} x={x as number} y={y as number} width={3.2} height={1.6} rx={0.6} fill={col as string} transform={`rotate(${(i * 47) % 180} ${x} ${y})`} />
            ))}
            <path d="M8 36 q6 -6 12 0 t12 0" fill="none" stroke="#22d3ee" strokeWidth={1.2} />
          </>}
          {w.has("lunettes") && <>
            <path d="M60 35.5 L70 35" stroke="#111827" strokeWidth={0.9} />
            <rect x={69.5} y={34} width={9.5} height={4} rx={2} fill="#111827" />
            <path d="M71 35 h3" stroke="#60a5fa" strokeWidth={0.8} />
          </>}
          {w.has("frontale") && <>
            <path d="M79 33 L120 22 L120 50Z" fill="#fde047" opacity={sp.ambiance === "nuit" ? ".45" : ".25"} />
            <rect x={77} y={31} width={4} height={4} rx={1} fill="#facc15" stroke="#a16207" strokeWidth={0.5} />
          </>}
          {/* bras avant (devant le torse) */}
          <Limb pts={[[67, 56], [77, 70], [87, 62]]} color={skin} w={5.5} />
          {w.has("gants") && <><circle cx={87} cy={62} r={2.8} fill="#334155" /><circle cx={44} cy={62} r={2.8} fill="#334155" /></>}
          {w.has("poignets") && <>
            <rect x={78} y={66.5} width={4.5} height={3} rx={1} fill="#ef4444" transform="rotate(-40 80 68)" />
            <rect x={45} y={64.5} width={4.5} height={3} rx={1} fill="#ef4444" transform="rotate(48 47 66)" />
          </>}
          {w.has("montre") && <rect x={82.5} y={63} width={4} height={3.4} rx={1} fill="#111827" transform="rotate(-40 84.5 64.7)" />}
        </g>
      </g>
    </svg>
  );
}

/** Image Gemini si elle est prête (retour à l'avatar dessiné si elle ne charge pas), sinon l'avatar dessiné. */
export function AvatarPic({ c, size, head }: { c: Career; size: number; head: boolean }): ReactElement {
  const [failed, setFailed] = useState<string | null>(null);
  const im = c.avatar_image;
  if (im && im.ready && failed !== im.url) {
    return <img src={im.url} alt={`Avatar niveau ${c.level}`} className={head ? "av-img head" : "av-img"} width={size}
      height={head ? size : Math.round(size * 1.25)} loading="lazy" onError={() => setFailed(im.url)} />;
  }
  return <AvatarSvg cfg={c.avatar} level={c.level} gear={c.gear} scene={c.scene} place={c.place} ambiance={c.ambiance}
    size={size} head={head} />;
}
