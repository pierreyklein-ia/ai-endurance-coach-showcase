/* v0.47 : décor de l'avatar en deux choix qui se croisent — le lieu (le paysage) et l'ambiance (saison, neige, nuit).
 * Même règle que coach/career.py (split_scene / resolve_scene) ; ici seulement l'affichage et la palette. */

export const PLACES = ["route", "collines", "foret", "bord_de_mer", "montagne", "sommet", "ville", "volcan"] as const;
export const AMBIANCES = ["aucune", "printemps", "ete", "automne", "hiver", "neige", "nuit"] as const;
export type Place = (typeof PLACES)[number];
export type Ambiance = (typeof AMBIANCES)[number];

const isPlace = (x: unknown): x is Place => typeof x === "string" && (PLACES as readonly string[]).includes(x);
const isAmbiance = (x: unknown): x is Ambiance => typeof x === "string" && (AMBIANCES as readonly string[]).includes(x);

/** Lieu + ambiance à dessiner. Les valeurs explicites gagnent ; sinon un ancien décor unique (`scene`) est réparti :
 *  « hiver » → route + hiver, « montagne » → montagne sans ambiance. */
export function sceneParts(scene?: string | null, place?: string | null, ambiance?: string | null): { place: Place; ambiance: Ambiance } {
  let p: Place = isPlace(place) ? place : "route";
  let a: Ambiance = isAmbiance(ambiance) ? ambiance : "aucune";
  if (!isPlace(place) && isPlace(scene)) p = scene;
  if (!isAmbiance(ambiance) && isAmbiance(scene)) a = scene;
  return { place: p, ambiance: a };
}

/** Mélange deux couleurs #rrggbb (t = 0 → a, 1 → b). */
export function mix(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const ch = (s: number) => Math.round(((pa >> s) & 255) * (1 - t) + ((pb >> s) & 255) * t);
  return "#" + ((1 << 24) | (ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).slice(1);
}

export interface Palette {
  sky0: string; sky1: string; far: string; farShade: string; grass: string; grassShade: string;
  foliage: string; foliageShade: string; foliageAlt: string; pine: string; pineShade: string; trunk: string;
  sand: string; sandShade: string; sea: string; seaDeep: string; foam: string;
  rock: string; rockShade: string; cap: string; building: string; buildingShade: string; window: string; roof: string;
  lava: string; smoke: string; ground: string; groundEdge: string; cloud: string;
  /** neige sur les reliefs : 0 = aucune, 0.25 = sommets, 0.6 = tout le haut */
  caps: number;
  /** arbres feuillus : feuillage, fleurs (printemps), nus (hiver), nus et enneigés (neige) */
  trees: "leaf" | "blossom" | "bare" | "snowy";
  flakes: number; sun: "none" | "sun" | "big" | "pale" | "sunrise" | "moon";
}

const BASE: Record<Place, Partial<Palette>> = {
  route: { sky0: "#bfdbfe", sky1: "#eff6ff", far: "#a7c7e7", farShade: "#93b4d8", ground: "#d6d3d1", groundEdge: "#a8a29e" },
  collines: { sky0: "#bae6fd", sky1: "#f0f9ff", far: "#a7e3b9", farShade: "#86d39d", ground: "#c9a47a", groundEdge: "#a47c52" },
  foret: { sky0: "#c7ecd5", sky1: "#f0fdf4", far: "#86c79a", farShade: "#6bb582", ground: "#b08657", groundEdge: "#8a6440" },
  bord_de_mer: { sky0: "#7dd3fc", sky1: "#e0f2fe", ground: "#fde68a", groundEdge: "#f5c84c" },
  montagne: { sky0: "#c7d2fe", sky1: "#eef2ff", far: "#b4bff5", farShade: "#a3aeea", ground: "#b8b2aa", groundEdge: "#8f877d" },
  sommet: { sky0: "#fdba74", sky1: "#fde68a", far: "#f5c49a", farShade: "#e8ad80", rock: "#b45309", rockShade: "#92400e",
    ground: "#8a7f74", groundEdge: "#6b6158" },
  ville: { sky0: "#c7d2fe", sky1: "#e0e7ff", ground: "#cbd5e1", groundEdge: "#94a3b8" },
  volcan: { sky0: "#fed7aa", sky1: "#fff7ed", far: "#9fc5b0", farShade: "#7fae96", rock: "#57534e", rockShade: "#44403c",
    ground: "#57534e", groundEdge: "#3f3a36" },
};
const COMMON: Palette = {
  sky0: "#bfdbfe", sky1: "#eff6ff", far: "#a7c7e7", farShade: "#93b4d8", grass: "#86efac", grassShade: "#4ade80",
  foliage: "#22c55e", foliageShade: "#15803d", foliageAlt: "#16a34a", pine: "#16a34a", pineShade: "#166534", trunk: "#92400e",
  sand: "#fde68a", sandShade: "#fbbf24", sea: "#22d3ee", seaDeep: "#0891b2", foam: "#ffffff",
  rock: "#818cf8", rockShade: "#6366f1", cap: "#ffffff", building: "#94a3b8", buildingShade: "#64748b", window: "#e0f2fe",
  roof: "#64748b", lava: "#f97316", smoke: "#a8a29e", ground: "#d6d3d1", groundEdge: "#a8a29e", cloud: "#ffffff",
  caps: 0, trees: "leaf", flakes: 0, sun: "none",
};
const SNOW = "#f8fafc", SNOW_SHADE = "#dbe7f5", NIGHT = "#141a3a";
const GROUND_KEYS = ["far", "farShade", "grass", "grassShade", "foliage", "foliageShade", "foliageAlt", "pine", "pineShade",
  "trunk", "sand", "sandShade", "sea", "seaDeep", "rock", "rockShade", "building", "buildingShade", "roof", "ground",
  "groundEdge", "smoke"] as const;

/** Couleurs et effets d'un décor : le lieu donne le paysage, l'ambiance le teinte (palette) et ajoute ses effets. */
export function palette(place: Place, ambiance: Ambiance): Palette {
  const p: Palette = { ...COMMON, ...BASE[place] };
  if (place === "montagne" || place === "sommet") p.caps = 0.22;
  if (place === "bord_de_mer" || place === "sommet") p.sun = place === "sommet" ? "sunrise" : "sun";
  const tint = (to: string, t: number, keys: readonly (keyof Palette)[] = GROUND_KEYS) => {
    for (const k of keys) (p as unknown as Record<string, string>)[k] = mix(p[k] as string, to, t);
  };
  switch (ambiance) {
    case "printemps":
      Object.assign(p, { sky0: "#a5dcfb", sky1: "#fdf2f8", grass: "#8bf0a6", grassShade: "#4ade80", foliage: "#4ade80",
        foliageShade: "#22a855", trees: "blossom", sun: place === "sommet" ? "sunrise" : "sun" });
      if (place === "collines" || place === "route") { p.far = "#b5ecc4"; p.farShade = "#96dfab"; }
      break;
    case "ete":
      Object.assign(p, { sky0: "#38bdf8", sky1: "#fef3c7", grass: "#d9e46a", grassShade: "#c2cc4a", foliage: "#3fb950",
        foliageShade: "#1f8a3a", sun: "big", sand: "#fde58a" });
      if (place === "collines") { p.far = "#e9d36a"; p.farShade = "#d8bd4c"; }
      if (place === "sommet") Object.assign(p, { sky0: "#60a5fa", sky1: "#fde68a", rock: "#a16207", rockShade: "#854d0e" });
      break;
    case "automne":
      Object.assign(p, { sky0: "#fdc58e", sky1: "#fff4e6", grass: "#d6a24a", grassShade: "#b7832f", foliage: "#ea580c",
        foliageShade: "#c2410c", foliageAlt: "#dc2626", pine: "#3f6b2a", pineShade: "#2f5120", sun: "sun" });
      if (place === "collines" || place === "route" || place === "foret") { p.far = "#d9a45c"; p.farShade = "#c58c44"; }
      if (place === "sommet") p.sun = "sunrise";
      break;
    case "hiver":
      tint("#cbd5e1", 0.35);
      Object.assign(p, { sky0: "#b8c6d9", sky1: "#eef2f7", grass: "#dfe7ef", grassShade: "#c5d1de", sand: "#e9e3cf",
        sandShade: "#d6cdb2", caps: place === "volcan" ? 0.2 : Math.max(p.caps, 0.3), trees: "bare", flakes: 6, sun: "pale",
        ground: mix(p.ground, "#e2e8f0", 0.55), cloud: "#f1f5f9", roof: "#eef2f7" });
      break;
    case "neige":
      tint("#94a3b8", 0.2);
      Object.assign(p, { sky0: "#9fb1c7", sky1: "#e8eef5", far: SNOW_SHADE, farShade: "#c3d3e6", grass: SNOW,
        grassShade: SNOW_SHADE, sand: SNOW, sandShade: SNOW_SHADE, ground: SNOW, groundEdge: "#c3d3e6", roof: SNOW,
        sea: "#1f9fb5", seaDeep: "#0e6f85", caps: 0.6, trees: "snowy", flakes: 22, sun: "none", cloud: "#e2e8f0" });
      break;
    case "nuit":
      tint(NIGHT, 0.55);
      Object.assign(p, { sky0: "#0b1026", sky1: "#2e2a78", cap: mix("#ffffff", NIGHT, 0.3), window: "#fde047",
        foam: "#c7d2fe", cloud: "#3b3f74", sun: "moon", lava: "#fb923c" });
      if (p.caps > 0.25) p.caps = 0.25;
      break;
    default:
      break;
  }
  return p;
}
